"""Read-only OpenD adapters for the custody dryrun live path.

Hard guarantee
--------------
Everything here uses ``OpenQuoteContext`` only. This module never imports or
calls ``OpenSecTradeContext``, ``unlock_trade``, ``place_order`` or any other
broker mutation API. The dryrun runner combines it with
``CustodyService(mode='dryrun')``, which refuses to dispatch orders, and a
``Controller`` that is constructed without a broker.

``futu-api`` is imported lazily so unit tests can run without it and so this
file can be inspected/source-scanned without side effects.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
import math
import os
import re
from zoneinfo import ZoneInfo

from .models import Contract, Quote, Session, ET, instant


DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 11111

# Futu US option code: US.<underlying><YYMMDD><C|P><strike in thousandths>.
_OPTION_CODE = re.compile(r'^US\.([A-Z][A-Z0-9.\-]*?)(\d{6})([CP])(\d{3,})$')

FORBIDDEN_TRADE_NAMES = (
    'OpenSecTradeContext',
    'place_order',
    'unlock_trade',
    'modify_order',
    'cancel_all_order',
)


def _futu():
    try:
        import futu  # noqa: WPS433 - intentionally lazy
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError('futu-api is required for the OpenD dryrun path; pip install futu-api') from exc
    return futu


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _records(frame):
    """Convert a futu/pandas DataFrame to a plain list of dicts without importing pandas."""
    if frame is None:
        return []
    if hasattr(frame, 'to_dict'):
        try:
            return list(frame.to_dict(orient='records'))
        except TypeError:  # pragma: no cover - defensive
            return list(frame.to_dict('records'))
    return list(frame)


def _et(value):
    """Parse an OpenD market-local timestamp into an aware ET datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=ET)
    text = str(value).strip()
    if not text or text.upper().startswith('NAT'):
        return None
    text = text.replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(text[: len(fmt) + 2], fmt).replace(tzinfo=ET)
        except ValueError:
            continue
    return None


def _day(value):
    parsed = _et(value)
    return parsed.date().isoformat() if parsed else None


def parse_option_code(code):
    """Return (underlying, expiry ISODate, right) for a Futu US option code."""
    if not isinstance(code, str):
        raise ValueError('option contract code required')
    match = _OPTION_CODE.match(code.strip().upper())
    if not match:
        raise ValueError('not a US option contract code: ' + str(code))
    underlying, yymmdd, cp = match.group(1), match.group(2), match.group(3)
    expiry = date(2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])).isoformat()
    return underlying, expiry, ('CALL' if cp == 'C' else 'PUT')


class OpenDMarket:
    """Thin read-only quote wrapper. Every method is observation only."""

    def __init__(self, host=None, port=None, quote_context=None):
        self.host = host or os.environ.get('FUTU_HOST', DEFAULT_HOST)
        self.port = int(port or os.environ.get('FUTU_PORT', DEFAULT_PORT))
        self._ctx = quote_context
        self._owns_ctx = quote_context is None

    @property
    def context(self):
        if self._ctx is None:
            futu = _futu()
            self._ctx = futu.OpenQuoteContext(host=self.host, port=self.port)
        if type(self._ctx).__name__ in FORBIDDEN_TRADE_NAMES or 'Trade' in type(self._ctx).__name__:
            raise RuntimeError('read-only guarantee: trade context rejected')
        return self._ctx

    def close(self):
        if self._ctx is not None and self._owns_ctx:
            try:
                self._ctx.close()
            finally:
                self._ctx = None
        else:
            self._ctx = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def snapshot(self, codes):
        """Return ``{code: row}`` for the requested codes. Read-only."""
        codes = [str(c).upper() for c in codes]
        ret, data = self.context.get_market_snapshot(codes)
        if ret != 0:
            raise RuntimeError('get_market_snapshot failed: ' + str(data))
        return {str(row.get('code', '')).upper(): row for row in _records(data)}

    def quote(self, contract, now=None):
        """Return a fresh ``Quote`` or ``None`` when bid/ask is missing/crossed."""
        contract = str(contract).upper()
        row = self.snapshot([contract]).get(contract)
        if row is None:
            raise KeyError('contract not present in OpenD snapshot: ' + contract)
        bid, ask = _num(row.get('bid_price')), _num(row.get('ask_price'))
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            return None
        as_of = _et(row.get('update_time')) or (instant(now) if now is not None else datetime.now(ET))
        return Quote(contract, bid, ask, as_of)

    def underlying_mark(self, symbol, now=None):
        """Return the underlying last price or ``None`` when unavailable."""
        code = str(symbol).upper()
        if not code.startswith('US.'):
            code = 'US.' + code
        row = self.snapshot([code]).get(code)
        if row is None:
            return None
        return _num(row.get('last_price'))

    def subscribe(self, codes, subtypes=None):
        """Subscribe for streaming quotes; returns the OpenD message."""
        futu = _futu()
        subs = subtypes or [futu.SubType.QUOTE, futu.SubType.K_1M]
        ret, msg = self.context.subscribe([str(c).upper() for c in codes], list(subs))
        if ret != 0:
            raise RuntimeError('subscribe failed: ' + str(msg))
        return msg

    def unsubscribe_all(self):
        try:
            self.context.unsubscribe_all()
        except Exception:  # noqa: BLE001 - best effort on shutdown
            pass

    def trading_days(self, start, end, market='US'):
        """OpenD exchange calendar. Used by the dryrun-only session calendar."""
        ret, data = self.context.request_trading_days(market=market, start=str(start), end=str(end))
        if ret != 0:
            raise RuntimeError('request_trading_days failed: ' + str(data))
        return _records(data)

    def request_kline(self, code, ktype, start, end, max_count=1000):
        """Page ``request_history_kline`` and return a list of row dicts."""
        futu = _futu()
        kl = getattr(futu.KLType, ktype)
        rows, page = [], None
        while True:
            ret, data, page = self.context.request_history_kline(
                str(code).upper(), start=str(start), end=str(end), ktype=kl,
                autype=futu.AuType.NONE, max_count=max_count, page_req_key=page,
            )
            if ret != 0:
                raise RuntimeError('request_history_kline failed for %s %s: %s' % (code, ktype, data))
            rows.extend(_records(data))
            if page is None:
                break
        return rows

    def current_kline(self, code, count, ktype):
        """Real-time K-line (requires a prior K-subscription); read-only."""
        futu = _futu()
        ret, data = self.context.get_cur_kline(str(code).upper(), int(count), getattr(futu.KLType, ktype))
        if ret != 0:
            raise RuntimeError('get_cur_kline failed for %s %s: %s' % (code, ktype, data))
        return _records(data)


class OpenDContractResolver:
    """Resolve exact broker option metadata from OpenD; never guesses."""

    def __init__(self, market):
        self.market = market

    def resolve(self, code):
        underlying, expiry, right_from_code = parse_option_code(code)
        code = str(code).strip().upper()
        row = self.market.snapshot([code]).get(code)
        if row is None:
            raise ValueError('contract not found in OpenD: ' + code)
        raw_right = str(row.get('option_type', '')).strip().upper()
        right = raw_right if raw_right in ('CALL', 'PUT') else right_from_code
        expiry = _day(row.get('strike_time')) or expiry
        strike = _num(row.get('option_strike_price'))
        if strike is None:
            strike = int(_OPTION_CODE.match(code).group(4)) / 1000.0
        multiplier = int(_num(row.get('option_contract_multiplier')) or 100)
        status = str(row.get('sec_status', 'NORMAL')).strip().upper()
        tradable = bool(row.get('option_valid')) and status in ('', 'NORMAL')
        # ``lot_size`` in the snapshot is the contract size (100 shares), not the
        # orderable lot; US options are orderable in single-contract units.
        return Contract(code, underlying, expiry, right, strike, multiplier, 1, 'USD', tradable)


class OpenDTradingCalendar:
    """Real OpenD trading calendar (WHOLE vs MORNING early close). Dryrun only.

    It is still a valid calendar adapter for observation: the service mode is
    ``dryrun`` and can never submit orders, so an early-close guess cannot cause
    a live liquidation.
    """

    def __init__(self, market, market_code='US'):
        self.market = market
        self.market_code = market_code
        self._cache = {}

    def session(self, day):
        day = date.fromisoformat(day).isoformat()
        rows = self._cache.get(day)
        if rows is None:
            rows = self.market.trading_days(day, day, self.market_code)
            self._cache[day] = rows
        row = next((r for r in rows if str(r.get('time'))[:10] == day), None)
        if row is None:
            raise ValueError('not a %s trading day per OpenD: %s' % (self.market_code, day))
        early = str(row.get('trade_date_type', 'WHOLE')).strip().upper() != 'WHOLE'
        d = date.fromisoformat(day)
        return Session(day, datetime.combine(d, dtime(9, 30), tzinfo=ET),
                       datetime.combine(d, dtime(13, 0) if early else dtime(16, 0), tzinfo=ET))
