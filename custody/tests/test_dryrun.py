import ast
import io
import tempfile
import unittest
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

from custody.controller import Controller
from custody.dryrun import DryRunRunner, SignalFrameSource, bar_boundary, build_argument_parser
from custody.models import Contract, Frame, Quote, Session, ET
from custody.opend import (OpenDContractResolver, OpenDMarket, OpenDTradingCalendar,
                           parse_option_code)
from custody.service import CustodyService

DAY = '2026-09-14'
FUTURE = '2026-09-18'
T = datetime(2026, 9, 14, 10, 0, tzinfo=ET)
CONTRACT = 'US.SKHY260918P175000'
SID = 'orb_rvol_rsi_1m_v1'


class Catalog:
    def resolve(self, code):
        if code != CONTRACT: raise ValueError('unknown contract')
        return Contract(CONTRACT, 'SKHY', FUTURE, 'PUT', 175.0, 100, 1, 'USD', True)


class Calendar:
    def session(self, day):
        if day != DAY: raise ValueError('session not configured')
        return Session(day, T.replace(hour=9, minute=30), T.replace(hour=16, minute=0))


class AnyCalendar:
    def session(self, day):
        d = datetime.fromisoformat(day)
        return Session(day, datetime.combine(d, dtime(9, 30), tzinfo=ET),
                       datetime.combine(d, dtime(16, 0), tzinfo=ET))


class FakeHistoryMarket:
    def __init__(self, daily, prior, current=None):
        self.daily, self.prior, self.current = daily, prior, current or []

    def request_kline(self, code, ktype, start, end, max_count=1000):
        return list(self.daily if ktype == 'K_DAY' else self.prior)

    def current_kline(self, code, count, ktype):
        return list(self.current)


class Rows:
    def __init__(self, rows): self._rows = list(rows)
    def to_dict(self, orient='records'): return list(self._rows)
    def __len__(self): return len(self._rows)


class FakeQuoteContext:
    def __init__(self, snapshot=None, trading_days=None, current=None, history=None):
        self.snapshot_rows = snapshot or []
        self.trading = trading_days or []
        self.current = current or []
        self.history = history or []
        self.subscribed = []
        self.closed = False
    def get_market_snapshot(self, codes):
        wanted = {str(c).upper() for c in codes}
        return 0, Rows([r for r in self.snapshot_rows if str(r.get('code', '')).upper() in wanted])
    def request_trading_days(self, market=None, start=None, end=None):
        return 0, list(self.trading)
    def get_cur_kline(self, code, count, ktype):
        return 0, Rows(self.current)
    def request_history_kline(self, code, start=None, end=None, ktype=None, autype=None,
                              max_count=None, page_req_key=None):
        return 0, Rows(self.history), None
    def subscribe(self, codes, subtypes):
        self.subscribed.append(list(codes)); return 0, 'ok'
    def unsubscribe_all(self): return 0, 'ok'
    def close(self): self.closed = True


class FakeMarket:
    """Minimal Market adapter for the runner; records nothing that can mutate."""
    def __init__(self, quote=None, mark=None):
        self._quote, self._mark = quote, mark
    def quote(self, contract, now=None): return self._quote
    def underlying_mark(self, symbol, now=None): return self._mark


class FakeFrameSource:
    def __init__(self, frame=None, error=None):
        self.frame_value, self.error, self.calls = frame, error, 0
    def frame(self, job, now=None):
        self.calls += 1
        if self.error is not None: raise self.error
        return self.frame_value


def good_quote(when=T):
    return Quote(CONTRACT, 1.00, 1.05, when)


def good_frame(service, when=T, ready=True, close=100.0):
    return Frame('SKHY', service.registry.get(SID)['sha256'], when, 1, close, 2.0, ready)


def make_dryrun(tmp_path):
    service = CustodyService(tmp_path, 'dryrun-test', Catalog(), Calendar(), mode='dryrun')
    job = service.create_job({'strategy_id': SID, 'symbol': 'SKHY', 'direction': 'SHORT',
                              'contract': CONTRACT, 'trade_date': DAY}, T)
    return service, job


def run_one_tick(service, job, frame_source):
    events = []
    runner = DryRunRunner(service, FakeMarket(good_quote()), job['id'], frame_source=frame_source,
                          logger=lambda event, **fields: events.append({'event': event, **fields}))
    state = runner.tick(T)
    return runner, state, events


class DryRunSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'dryrun.sqlite'

    def tearDown(self):
        self.tmp.cleanup()

    # 1. Contract validation is relaxed only for dryrun.
    def test_future_expiry_allowed_only_in_dryrun(self):
        service, job = make_dryrun(self.path)
        self.assertEqual(job['request']['trade_date'], DAY)
        self.assertEqual(job['contract']['expiry'], FUTURE)
        paper = CustodyService(Path(self.tmp.name) / 'paper.sqlite', 'paper-test', Catalog(), Calendar(), mode='paper')
        with self.assertRaisesRegex(ValueError, 'expire on trade_date'):
            paper.create_job({'strategy_id': SID, 'symbol': 'SKHY', 'direction': 'SHORT',
                              'contract': CONTRACT, 'trade_date': DAY}, T)

    # 2. The service itself refuses to dispatch in dryrun, even with a spy adapter.
    def test_dryrun_dispatch_is_hard_blocked(self):
        service, job = make_dryrun(self.path)
        service.on_frame(job['id'], good_frame(service), T, good_quote())
        self.assertEqual(service.get_job(job['id'])['orders'][0]['status'], 'CREATED')

        class SpyAdapter:
            account, mode = 'dryrun-test', 'dryrun'
            def __init__(self): self.calls = []
            def submit(self, intent): self.calls.append(intent); raise AssertionError('must never submit')
            def cancel(self, target, key): self.calls.append((target, key))
            def lookup(self, key): return None
        spy = SpyAdapter()
        with self.assertRaisesRegex(ValueError, 'dryrun'):
            service.dispatch_next(spy, T)
        self.assertEqual(spy.calls, [])

    # 3. A controller without a broker exists only for dryrun.
    def test_controller_requires_broker_outside_dryrun(self):
        service, _ = make_dryrun(self.path)
        Controller(service)  # no broker is valid in dryrun
        paper = CustodyService(Path(self.tmp.name) / 'paper2.sqlite', 'paper2', Catalog(), Calendar(), mode='paper')
        with self.assertRaisesRegex(ValueError, 'dryrun'):
            Controller(paper)

    # 4. The runner advances the controller, logs intents and leaves them local.
    def test_runner_logs_intents_and_never_dispatches(self):
        service, job = make_dryrun(self.path)
        runner, state, events = run_one_tick(service, job, FakeFrameSource(good_frame(service)))
        self.assertEqual(state['state'], 'ENTRY')
        orders = state['orders']
        self.assertEqual([o['side'] for o in orders], ['BUY_OPEN'])
        self.assertEqual(orders[0]['status'], 'CREATED')          # never DISPATCHING/OPEN/FILLED
        self.assertEqual(orders[0]['cumulative_qty'], 0)
        self.assertFalse(any(o['status'] in {'DISPATCHING', 'UNKNOWN', 'OPEN', 'PARTIAL', 'FILLED'}
                             for o in orders))
        intents = [e for e in events if e['event'] == 'order_intent']
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]['dryrun'], True)
        self.assertEqual(intents[0]['submitted'], False)
        self.assertEqual(intents[0]['side'], 'BUY_OPEN')
        self.assertNotIn('order_error', {e['event'] for e in events})

    # 5. Web/resident loop keeps running when history/frames are unavailable.
    def test_frame_error_is_non_fatal_and_no_order(self):
        service, job = make_dryrun(self.path)
        _runner, state, events = run_one_tick(service, job, FakeFrameSource(error=ValueError('no history')))
        self.assertEqual(state['orders'], [])
        self.assertIn('frame_error', {e['event'] for e in events})

    # 6. Even a stale/heavy path cannot reach a broker in dryrun: no status moves past CREATED.
    def test_repeated_ticks_keep_intents_local(self):
        service, job = make_dryrun(self.path)
        runner, state, events = run_one_tick(service, job, FakeFrameSource(good_frame(service)))
        later = T + timedelta(seconds=2)
        runner.market = FakeMarket(good_quote(later), 99.0)
        state = runner.tick(later)
        self.assertEqual(state['orders'][0]['status'], 'CREATED')
        self.assertEqual(len([e for e in events if e['event'] == 'order_intent']), 1)  # logged once

    # 6b. Explicit spy: the runner never even calls dispatch_next.
    def test_runner_never_calls_dispatch_next(self):
        service, job = make_dryrun(self.path)
        calls = []
        service.dispatch_next = lambda *a, **k: calls.append(a)
        _runner, state, _events = run_one_tick(service, job, FakeFrameSource(good_frame(service)))
        self.assertEqual(calls, [])
        self.assertEqual(state['orders'][0]['status'], 'CREATED')

    # 7. Static proof the dryrun modules cannot call trade APIs.
    def test_dryrun_sources_reference_no_trade_api(self):
        forbidden = {'OpenSecTradeContext', 'place_order', 'unlock_trade', 'modify_order'}
        for name in ('opend.py', 'dryrun.py'):
            tree = ast.parse((Path(__file__).resolve().parents[1] / name).read_text())
            used = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute): used.add(node.attr)
                elif isinstance(node, ast.Name): used.add(node.id)
                elif isinstance(node, ast.ImportFrom): used.update(a.name for a in node.names)
                elif isinstance(node, ast.Import): used.update(a.name.split('.')[-1] for a in node.names)
            self.assertEqual(used & forbidden, set(), name)


class OpenDAdapterTests(unittest.TestCase):
    def test_parse_option_code(self):
        self.assertEqual(parse_option_code(CONTRACT), ('SKHY', FUTURE, 'PUT'))
        self.assertEqual(parse_option_code('US.SPY260918C600000'), ('SPY', FUTURE, 'CALL'))
        with self.assertRaises(ValueError):
            parse_option_code('SKHY260918P175000')

    def test_contract_resolver_uses_snapshot_metadata(self):
        row = {'code': CONTRACT, 'option_type': 'PUT', 'option_strike_price': 175.0,
               'strike_time': FUTURE, 'option_contract_multiplier': 100.0,
               'option_valid': True, 'sec_status': 'NORMAL'}
        market = OpenDMarket(quote_context=FakeQuoteContext(snapshot=[row]))
        contract = OpenDContractResolver(market).resolve(CONTRACT)
        self.assertEqual((contract.code, contract.underlying, contract.expiry, contract.right),
                         (CONTRACT, 'SKHY', FUTURE, 'PUT'))
        self.assertEqual((contract.strike, contract.multiplier, contract.lot_size, contract.tradable),
                         (175.0, 100, 1, True))
        # A snapshot that disagrees with the code still wins (never guess from text).
        row2 = dict(row, option_type='CALL')
        market2 = OpenDMarket(quote_context=FakeQuoteContext(snapshot=[row2]))
        self.assertEqual(OpenDContractResolver(market2).resolve(CONTRACT).right, 'CALL')

    def test_market_quote_and_mark_parsing(self):
        snapshot = [
            {'code': CONTRACT, 'bid_price': 4.35, 'ask_price': 4.50, 'update_time': '2026-09-14 10:00:00'},
            {'code': 'US.SKHY', 'last_price': 176.86},
        ]
        market = OpenDMarket(quote_context=FakeQuoteContext(snapshot=snapshot))
        quote = market.quote(CONTRACT, T)
        self.assertEqual((quote.contract, quote.bid, quote.ask), (CONTRACT, 4.35, 4.50))
        self.assertEqual(quote.as_of.isoformat(), T.isoformat())
        self.assertEqual(market.underlying_mark('US.SKHY'), 176.86)
        empty = OpenDMarket(quote_context=FakeQuoteContext(snapshot=[dict(snapshot[0], bid_price=0)]))
        self.assertIsNone(empty.quote(CONTRACT, T))

    def test_calendar_regular_and_early_close(self):
        days = [{'time': DAY, 'trade_date_type': 'WHOLE'},
                {'time': '2026-11-27', 'trade_date_type': 'MORNING'}]
        market = OpenDMarket(quote_context=FakeQuoteContext(trading_days=days))
        calendar = OpenDTradingCalendar(market)
        self.assertEqual(calendar.session(DAY).closes.strftime('%H:%M'), '16:00')
        self.assertEqual(calendar.session('2026-11-27').closes.strftime('%H:%M'), '13:00')
        with self.assertRaisesRegex(ValueError, 'trading day'):
            calendar.session('2026-09-19')

    def test_context_guard_rejects_trade_like_class(self):
        class OpenSecTradeContext:  # noqa: N801 - mimics a forbidden class name
            pass
        with self.assertRaisesRegex(RuntimeError, 'read-only'):
            OpenDMarket(quote_context=OpenSecTradeContext()).context


class FrameSourceTests(unittest.TestCase):
    def test_bar_boundary_floors_to_native_bar(self):
        self.assertIsNone(bar_boundary(T.replace(hour=9, minute=30, second=30), 1))
        self.assertEqual(bar_boundary(T.replace(hour=9, minute=31, second=45), 1),
                         T.replace(hour=9, minute=31, second=0))
        self.assertEqual(bar_boundary(T.replace(hour=9, minute=37, second=5), 5),
                         T.replace(hour=9, minute=35, second=0))
        self.assertIsNone(bar_boundary(T.replace(hour=9, minute=33), 5))
        with self.assertRaises(ValueError):
            bar_boundary(T, 3)

    def test_frame_source_caches_per_boundary_and_expires(self):
        service = CustodyService(Path(tempfile.mkdtemp()) / 'f.sqlite', 'f', Catalog(), Calendar(), mode='dryrun')
        job = service.create_job({'strategy_id': SID, 'symbol': 'SKHY', 'direction': 'SHORT',
                                  'contract': CONTRACT, 'trade_date': DAY}, T)

        class Provider:
            def __init__(self): self.calls = 0
            def evaluate(self, *a, **k):
                self.calls += 1
                return good_frame(service, when=k['as_of'] if 'as_of' in k else a[3])
        provider = Provider()

        class History:
            def __init__(self): self.calls = 0
            def collect(self, boundary):
                self.calls += 1
                return [], [], {}
        history = History()
        source = SignalFrameSource(provider, history, SID, 'SHORT')
        first = source.frame(job, T)
        self.assertIsNotNone(first)
        self.assertEqual((provider.calls, history.calls), (1, 1))
        source.frame(job, T + timedelta(seconds=3))  # same boundary -> cached
        self.assertEqual((provider.calls, history.calls), (1, 1))
        # Older than max_age -> no frame, no extra evaluation.
        self.assertIsNone(source.frame(job, T + timedelta(seconds=30)))
        self.assertEqual((provider.calls, history.calls), (1, 1))


class HistorySourceTests(unittest.TestCase):
    def test_history_source_normalizes_filters_and_dedupes(self):
        from custody.dryrun import OpenDHistorySource
        daily = [
            {'time_key': '2026-09-11', 'open': 1, 'high': 2, 'low': .5, 'close': 1.5},
            {'time_key': '2026-09-14', 'open': 1, 'high': 2, 'low': .5, 'close': 1.5},
        ]
        prior = [
            {'time_key': '2026-09-11 09:31:00', 'open': 1, 'high': 2, 'low': .5, 'close': 1.5, 'volume': 10},
            {'time_key': '2026-09-11 09:32:00', 'open': 1, 'high': 2, 'low': .5, 'close': 1.6, 'volume': 11},
        ]
        current = [
            {'time_key': '2026-09-14 09:31:00', 'open': 1, 'high': 2, 'low': .5, 'close': 1.7, 'volume': 12},
            {'time_key': '2026-09-14 10:05:00', 'open': 1, 'high': 2, 'low': .5, 'close': 1.8, 'volume': 13},
        ]
        market = FakeHistoryMarket(daily, prior, current)
        source = OpenDHistorySource(market, AnyCalendar(), 'US.SKHY', warmup_days=7, daily_days=40)
        bars, daily_rows, closes = source.collect(T)
        self.assertEqual([b['close_time'] for b in bars],
                         ['2026-09-11T09:31:00-04:00', '2026-09-11T09:32:00-04:00', '2026-09-14T09:31:00-04:00'])
        self.assertEqual([r['date'] for r in daily_rows], ['2026-09-11'])  # today excluded
        self.assertEqual(sorted(closes), ['2026-09-11', '2026-09-14'])


class CliTests(unittest.TestCase):
    def test_dryrun_cli_defaults(self):
        args = build_argument_parser().parse_args(
            ['--strategy', SID, '--symbol', 'US.SKHY', '--direction', 'SHORT', '--contract', CONTRACT])
        self.assertEqual((args.host, args.port, args.db, args.max_qty), ('127.0.0.1', 11111, '/tmp/custody-dryrun.sqlite', 1))
        self.assertFalse(args.once)

    def test_module_cli_accepts_dryrun(self):
        from custody.__main__ import main
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            main(['dryrun', '--strategy', SID, '--symbol', 'US.SKHY', '--direction', 'SHORT',
                  '--contract', CONTRACT, '--help'])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn('dryrun', out.getvalue().lower())


if __name__ == '__main__':
    unittest.main()
