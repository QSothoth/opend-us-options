"""Broker-neutral public input and trusted adapter messages. No network clients."""
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
import math

ET = ZoneInfo('America/New_York')


def instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone-aware timestamp required')
    return value


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(name + ' must be finite and positive')
    return float(value)


def symbol(value):
    if not isinstance(value, str): raise ValueError('symbol must be a string')
    value = value.upper().removeprefix('US.')
    if not value or len(value) > 12 or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ.-' for c in value):
        raise ValueError('invalid underlying symbol')
    return value


@dataclass(frozen=True)
class JobRequest:
    strategy_id: str
    symbol: str
    direction: str
    contract: str
    max_qty: int = 1
    trade_date: str | None = None

    @classmethod
    def parse(cls, payload, now):
        if not isinstance(payload, dict): raise ValueError('JSON object required')
        allowed = set(cls.__dataclass_fields__)
        if set(payload) - allowed: raise ValueError('unknown fields: ' + ','.join(sorted(set(payload)-allowed)))
        for k in ['strategy_id', 'symbol', 'direction', 'contract']:
            if not isinstance(payload.get(k), str) or not payload[k].strip(): raise ValueError(k + ' required')
        if payload['direction'] not in ('LONG', 'SHORT'): raise ValueError('direction must be LONG or SHORT')
        qty = payload.get('max_qty', 1)
        if type(qty) is not int or qty < 1: raise ValueError('max_qty must be a positive integer')
        day = payload.get('trade_date', instant(now).astimezone(ET).date().isoformat())
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day: raise ValueError('ISO trade_date required')
        return cls(payload['strategy_id'], symbol(payload['symbol']), payload['direction'], payload['contract'].strip(), qty, day)


@dataclass(frozen=True)
class Contract:
    code: str
    underlying: str
    expiry: str
    right: str
    strike: float
    multiplier: int = 100
    lot_size: int = 1
    currency: str = 'USD'
    tradable: bool = True

    def validate(self, request):
        if self.code != request.contract or symbol(self.underlying) != request.symbol:
            raise ValueError('resolved contract does not match request')
        if self.expiry != request.trade_date: raise ValueError('contract must expire on trade_date')
        if self.right != ('CALL' if request.direction == 'LONG' else 'PUT'): raise ValueError('contract right/direction mismatch')
        positive(self.strike, 'strike')
        if self.currency != 'USD' or self.tradable is not True: raise ValueError('contract not tradable USD option')
        if type(self.multiplier) is not int or self.multiplier <= 0 or type(self.lot_size) is not int or self.lot_size <= 0:
            raise ValueError('invalid contract sizing metadata')
        if request.max_qty % self.lot_size: raise ValueError('quantity does not match contract lot size')


@dataclass(frozen=True)
class Session:
    day: str
    opens: datetime
    closes: datetime

    def __post_init__(self):
        a, b = instant(self.opens).astimezone(ET), instant(self.closes).astimezone(ET)
        if a.date().isoformat() != self.day or b.date() != a.date() or b <= a:
            raise ValueError('invalid exchange session')


@dataclass(frozen=True)
class Quote:
    contract: str
    bid: float
    ask: float
    as_of: datetime


@dataclass(frozen=True)
class Frame:
    """Trusted indicator-provider output, never a public HTTP input."""
    symbol: str
    strategy_hash: str
    bar_close: datetime
    minutes: int
    close: float
    daily_atr: float
    entry_ready: bool


@dataclass(frozen=True)
class OrderUpdate:
    client_order_id: str
    sequence: int
    status: str
    cumulative_qty: int
    as_of: datetime
    underlying_mark: float | None = None
    average_option_price: float | None = None
    first_fill_at: datetime | None = None
