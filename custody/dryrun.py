"""Resident OpenD dryrun worker for custody (never submits an order).

The runner polls/subscribes real OpenD market data through the read-only
``OpenDMarket`` adapter and advances an existing custody job with a
``Controller`` that is built **without a broker**. Because the service runs in
``dryrun`` mode and ``CustodyService.dispatch_next`` refuses to dispatch there,
the only reachable outcome is a persisted order **intent** that is logged and
left local.

Entry point::

    python3 -m custody dryrun \\
        --strategy orb_rvol_rsi_1m_v1 \\
        --symbol US.SKHY --direction SHORT \\
        --contract US.SKHY260918P175000
"""
from __future__ import annotations

import argparse
import os
import urllib.request
from datetime import datetime, timedelta
import json
import time
from zoneinfo import ZoneInfo

from .controller import Controller
from .models import ET, instant
from .opend import (DEFAULT_HOST, DEFAULT_PORT, OpenDContractResolver,
                    OpenDMarket, OpenDTradingCalendar)
from .service import CustodyService
from .signals import SignalProvider

__all__ = ['DryRunRunner', 'SignalFrameSource', 'OpenDHistorySource', 'bar_boundary', 'main']


def bar_boundary(now, step):
    """Floor ``now`` to a completed native bar boundary inside the RTH session."""
    if step not in (1, 5): raise ValueError('unsupported signal_minutes: %r' % (step,))
    now = instant(now).astimezone(ET)
    minute = now.hour * 60 + now.minute
    if minute < 571 or minute > 960: return None
    floored = 570 + ((minute - 570) // step) * step
    if floored <= 570: return None
    return now.replace(hour=floored // 60, minute=floored % 60, second=0, microsecond=0)


def _json_logger(event, **fields):
    print(json.dumps({'event': event, **fields}, sort_keys=True, default=str), flush=True)


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out



def _ntfy_notify(url, title, body, priority='high', tags='chart_with_upwards_trend'):
    """Best-effort POST to an ntfy (or compatible) topic URL. Never raises."""
    if not url:
        return
    try:
        req = urllib.request.Request(str(url).strip(), data=body.encode('utf-8'), method='POST')
        req.add_header('Title', title[:250])
        req.add_header('Priority', priority)
        req.add_header('Tags', tags)
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
    except Exception:  # noqa: BLE001 - notify must not break dryrun
        pass


class OpenDHistorySource:
    """Collect the exact bars/daily/session inputs the feature worker requires.

    It is read-only and quota-aware: prior sessions are fetched once per run and
    today's stream is read with ``get_cur_kline`` when the caller has subscribed
    to ``K_1M``. Without a subscription it falls back to history, extending the
    already-fetched today range incrementally instead of re-requesting from
    midnight every boundary. A missing/gappy session simply raises later in the
    worker; the runner logs and continues polling.
    """

    def __init__(self, market, calendar, symbol, warmup_days=30, daily_days=120, use_current=True, logger=None):
        symbol = str(symbol).upper()
        self.market = market
        self.calendar = calendar
        self.code = symbol if symbol.startswith('US.') else 'US.' + symbol
        self.warmup_days = int(warmup_days)
        self.daily_days = int(daily_days)
        self.use_current = bool(use_current)
        self.log = logger or _json_logger
        self._daily = None
        self._daily_day = None
        self._prior = None
        self._prior_day = None
        # Today's history-fallback range is cached and only extended, never
        # re-requested from midnight on every boundary (history quota guard).
        self._today = None
        self._today_day = None
        self._today_end = None
        self._fallback_logged = False

    def collect(self, boundary):
        boundary = instant(boundary).astimezone(ET)
        day = boundary.date().isoformat()
        if self._daily is None or self._daily_day != day:
            start = (boundary.date() - timedelta(days=self.daily_days)).isoformat()
            rows = self.market.request_kline(self.code, 'K_DAY', start, day)
            self._daily = [r for r in self._normalize_daily(rows) if r['date'] < day]
            self._daily_day = day
        bars = self._intraday(boundary)
        closes = {}
        for session_day in sorted({bar['close_time'][:10] for bar in bars}):
            closes[session_day] = self.calendar.session(session_day).closes.isoformat()
        return bars, list(self._daily), closes

    def _intraday(self, boundary):
        day = boundary.date().isoformat()
        if self._prior is None or self._prior_day != day:
            start = (boundary.date() - timedelta(days=self.warmup_days)).isoformat() + ' 00:00:00'
            end = (boundary.date() - timedelta(days=1)).isoformat() + ' 23:59:59'
            self._prior = self._normalize_bars(self.market.request_kline(self.code, 'K_1M', start, end), None)
            self._prior_day = day
        today = []
        if self.use_current:
            try:
                today = self.market.current_kline(self.code, 600, 'K_1M')
            except Exception:  # noqa: BLE001 - falls back to history below
                today = []
        if today:
            today = self._normalize_bars(today, boundary)
        else:
            if not self._fallback_logged:
                self._fallback_logged = True
                self.log('history_fallback', code=self.code,
                         reason='get_cur_kline empty or unsubscribed',
                         start=day + ' 00:00:00')
            today = self._today_fallback(boundary, day)
        merged = self._prior + today
        dedup = {bar['close_time']: bar for bar in merged}
        return [dedup[key] for key in sorted(dedup)]

    def _today_fallback(self, boundary, day):
        """Fetch today's history incrementally, caching the range already seen."""
        from .opend import _et
        if self._today_day != day:
            self._today, self._today_day, self._today_end = [], day, None
        if self._today_end is not None and boundary <= self._today_end:
            return list(self._today)
        start = self._today_end.strftime('%Y-%m-%d %H:%M:%S') if self._today_end is not None else day + ' 00:00:00'
        rows = self.market.request_kline(self.code, 'K_1M', start, boundary.strftime('%Y-%m-%d %H:%M:%S'))
        merged = {bar['close_time']: bar for bar in self._today + self._normalize_bars(rows, boundary)}
        self._today = [merged[key] for key in sorted(merged)]
        self._today_end = _et(boundary) or boundary
        return list(self._today)

    @staticmethod
    def _normalize_bars(rows, boundary):
        from .opend import _et
        out = []
        for row in rows:
            when = _et(row.get('time_key'))
            if when is None or (boundary is not None and when > boundary): continue
            values = {key: _num(row.get(key)) for key in ('open', 'high', 'low', 'close', 'volume')}
            if any(values[key] is None for key in values): continue
            out.append({'close_time': when.isoformat(), **values})
        dedup = {bar['close_time']: bar for bar in out}
        return [dedup[key] for key in sorted(dedup)]

    @staticmethod
    def _normalize_daily(rows):
        from .opend import _day
        out = []
        for row in rows:
            day = _day(row.get('time_key'))
            if day is None: continue
            values = {key: _num(row.get(key)) for key in ('open', 'high', 'low', 'close')}
            if any(values[key] is None or values[key] <= 0 for key in values): continue
            out.append({'date': day, **values})
        dedup = {bar['date']: bar for bar in out}
        return [dedup[key] for key in sorted(dedup)]


class SignalFrameSource:
    """Evaluate the retained research strategy on live OpenD history.

    One evaluation per native bar boundary, cached until the next boundary.
    """

    def __init__(self, provider, history, strategy_id, direction, max_age_seconds=15):
        if direction not in ('LONG', 'SHORT'): raise ValueError('invalid direction')
        self.provider = provider
        self.history = history
        self.strategy_id = strategy_id
        self.direction = direction
        self.max_age_seconds = float(max_age_seconds)
        self._key = None
        self._frame = None

    def frame(self, job, now=None):
        now = instant(now) if now is not None else datetime.now(ET)
        step = job['strategy']['config']['case']['entry']['signal_minutes']
        boundary = bar_boundary(now, step)
        if boundary is None: return None
        key = boundary.isoformat()
        if key != self._key:
            # Commit the cache key only after a successful collect+evaluate so a
            # transient failure is retried on the next tick within this boundary.
            bars, daily, closes = self.history.collect(boundary)
            frame = self.provider.evaluate(self.strategy_id, job['request']['symbol'], self.direction,
                                           boundary, bars, daily, closes)
            self._key, self._frame = key, frame
        frame = self._frame
        if frame is None: return None
        if (now - instant(frame.bar_close)).total_seconds() > self.max_age_seconds: return None
        return frame


class DryRunRunner:
    """Resident loop: poll market data, advance the controller, log intents."""

    def __init__(self, service, market, job_id, frame_source=None, interval=5.0, logger=None, ntfy_url=None):
        if service.mode != 'dryrun': raise ValueError('DryRunRunner requires CustodyService(mode=dryrun)')
        self.service = service
        self.market = market
        self.job_id = job_id
        self.frame_source = frame_source
        self.interval = float(interval)
        self.log = logger or _json_logger
        self.ntfy_url = (ntfy_url or os.environ.get('CUSTODY_NTFY_URL') or '').strip() or None
        # Deliberately no broker: the only path to submit/cancel is unreachable.
        self.controller = Controller(service)
        self._seen_intents = set()

    def tick(self, now=None):
        now = instant(now) if now is not None else datetime.now(ET)
        job = self.service.get_job(self.job_id)
        contract, symbol = job['request']['contract'], job['request']['symbol']
        quote = mark = mark_as_of = frame = None
        try:
            quote = self.market.quote(contract, now)
        except Exception as exc:  # noqa: BLE001 - keep the resident loop alive
            self.log('quote_error', contract=contract, error=repr(exc))
        try:
            mark = self.market.underlying_mark(symbol, now)
            if mark is not None: mark_as_of = now
        except Exception as exc:  # noqa: BLE001
            self.log('underlying_error', symbol=symbol, error=repr(exc))
        if self.frame_source is not None:
            try:
                frame = self.frame_source.frame(job, now)
            except Exception as exc:  # noqa: BLE001 - history gaps are non-fatal in dryrun
                self.log('frame_error', symbol=symbol, error=repr(exc))
        try:
            state = self.controller.step(self.job_id, now, quote, frame, mark, mark_as_of)
        except ValueError as exc:  # stale/future bar or other service-side rejection
            self.log('step_error', job_id=self.job_id, error=repr(exc))
            return None
        self._log_new_intents(state)
        self.log('tick', state=state['state'], underlying_mark=mark, frame=frame is not None,
                 position_qty=state['position_qty'], attention=state['attention'],
                 quote=None if quote is None else {'bid': quote.bid, 'ask': quote.ask, 'as_of': quote.as_of.isoformat()})
        return state

    def _log_new_intents(self, state):
        for order in state['orders']:
            key = order['client_order_id']
            if key in self._seen_intents: continue
            self._seen_intents.add(key)
            self.log('order_intent', client_order_id=key, side=order['side'], kind=order['kind'],
                     contract=order['contract'], quantity=order['quantity'], limit_price=order['limit_price'],
                     position_effect=order['position_effect'], reason=order['reason'],
                     created_at=order['created_at'], dryrun=True, submitted=False)
            if self.ntfy_url:
                title = 'dryrun %s %s' % (order['side'], order['contract'])
                body = '%s qty=%s limit=%s\nreason=%s\nsubmitted=false\nid=%s' % (
                    order['kind'], order['quantity'], order['limit_price'], order.get('reason'), key)
                _ntfy_notify(self.ntfy_url, title, body)

    def run(self, ticks=None):
        executed = 0
        try:
            while ticks is None or executed < ticks:
                self.tick(datetime.now(ET))
                executed += 1
                if ticks is None or executed < ticks:
                    time.sleep(self.interval)
        except KeyboardInterrupt:
            self.log('stopped', reason='keyboard_interrupt')
        return executed


def build_argument_parser():
    parser = argparse.ArgumentParser(
        prog='custody dryrun',
        description='Poll local OpenD read-only and advance a custody job WITHOUT submitting orders.',
    )
    parser.add_argument('--strategy', required=True, help='registry strategy_id')
    parser.add_argument('--symbol', required=True, help='underlying, e.g. US.SKHY')
    parser.add_argument('--direction', choices=['LONG', 'SHORT'], required=True)
    parser.add_argument('--contract', required=True, help='exact Futu option code, e.g. US.SKHY260918P175000')
    parser.add_argument('--max-qty', type=int, default=1)
    parser.add_argument('--db', default='/tmp/custody-dryrun.sqlite', help='durable SQLite path')
    parser.add_argument('--account', default='opend-dryrun')
    parser.add_argument('--host', default=DEFAULT_HOST)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--interval', type=float, default=5.0, help='seconds between polls')
    parser.add_argument('--ticks', type=int, default=None, help='stop after N ticks (default: run forever)')
    parser.add_argument('--once', action='store_true', help='run a single tick')
    parser.add_argument('--no-subscribe', action='store_true',
                        help='snapshot only; frames fall back to cached 1m history')
    parser.add_argument('--no-frames', action='store_true', help='poll quotes only; skip strategy evaluation')
    parser.add_argument('--warmup-days', type=int, default=30, help='calendar days of 1m warmup for frames')
    parser.add_argument('--daily-days', type=int, default=120, help='calendar days of daily warmup for frames')
    parser.add_argument('--ntfy', default=None,
                        help='ntfy topic URL for order_intent pushes (or set CUSTODY_NTFY_URL)')
    return parser


def main(argv=None):
    args = build_argument_parser().parse_args(argv)
    market = OpenDMarket(host=args.host, port=args.port)
    subscribed = False
    try:
        resolver = OpenDContractResolver(market)
        calendar = OpenDTradingCalendar(market)
        service = CustodyService(args.db, args.account, resolver, calendar, mode='dryrun')
        now = datetime.now(ET)
        job = service.create_job({
            'strategy_id': args.strategy, 'symbol': args.symbol, 'direction': args.direction,
            'contract': args.contract, 'max_qty': args.max_qty,
        }, now)
        symbol = args.symbol if args.symbol.upper().startswith('US.') else 'US.' + args.symbol
        if not args.no_subscribe:
            try:
                market.subscribe([symbol, args.contract])
                subscribed = True
            except Exception as exc:  # noqa: BLE001 - snapshot polling still works
                _json_logger('subscribe_error', error=repr(exc))
        frame_source = None
        if not args.no_frames:
            frame_source = SignalFrameSource(SignalProvider(service.registry),
                                             OpenDHistorySource(market, calendar, symbol,
                                                                warmup_days=args.warmup_days,
                                                                daily_days=args.daily_days),
                                             args.strategy, args.direction)
        runner = DryRunRunner(service, market, job['id'], frame_source=frame_source, interval=args.interval,
                              ntfy_url=args.ntfy)
        _json_logger('dryrun_start', job_id=job['id'], mode=service.mode, strategy_id=args.strategy,
                     symbol=symbol, direction=args.direction, contract=args.contract,
                     state=job['state'], host=args.host, port=args.port, orders_never_submitted=True)
        runner.run(ticks=1 if args.once else args.ticks)
        return 0
    finally:
        if subscribed:
            market.unsubscribe_all()
        market.close()
