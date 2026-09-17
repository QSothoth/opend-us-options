"""Shared market-data types for the dataset, the daily freeze and the runner.

* :class:`Bar` - a normalized 1m (or daily) trade bar with an aware close time;
* :func:`normalize_bar_rows` / :func:`normalize_daily_rows` - OpenD rows to bars.

Honesty rule: 1m history is trade OHLCV for both the underlying and the option.
Option bid/ask exists only live; nothing here fabricates a spread. This module has
no network client and no order API.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from .models import ET, instant

__all__ = ['Bar', 'normalize_bar_rows', 'normalize_daily_rows']


def _num(value):
    """Return a finite float or ``None``; never a bool or NaN/inf."""
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


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


@dataclass(frozen=True)
class Bar:
    """A normalized trade bar. ``close_time`` is the aware bar-close timestamp."""

    code: str
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    interval: str = '1m'
    source: str = 'opend_kline'

    def __post_init__(self):
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError('bar code required')
        if not isinstance(self.interval, str) or not self.interval:
            raise ValueError('bar interval required')
        instant(self.close_time)
        for name in ('open', 'high', 'low', 'close', 'volume'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('non-finite bar ' + name)
        if self.volume < 0:
            raise ValueError('negative bar volume')
        if self.high < self.low:
            raise ValueError('bar high below low')
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError('invalid bar OHLC range')


def normalize_bar_rows(rows, code, interval='1m', boundary=None, source='opend_kline'):
    """Normalize raw OpenD kline rows into deduped, ordered :class:`Bar` objects.

    Rows with missing/non-finite values, a malformed timestamp or an impossible
    OHLC range are dropped; ``boundary`` drops anything after that bar close.
    """
    code = str(code).upper()
    limit = instant(boundary) if boundary is not None else None
    out = []
    for row in rows:
        if not hasattr(row, 'get'):
            continue
        when = _et(row.get('time_key'))
        if when is None or (limit is not None and when > limit):
            continue
        values = {key: _num(row.get(key)) for key in ('open', 'high', 'low', 'close', 'volume')}
        if any(values[key] is None for key in values):
            continue
        if min(values['open'], values['high'], values['low'], values['close']) <= 0:
            continue
        try:
            out.append(Bar(code, when, values['open'], values['high'], values['low'],
                           values['close'], values['volume'], interval, source))
        except ValueError:
            continue
    dedup = {bar.close_time: bar for bar in out}
    return [dedup[key] for key in sorted(dedup)]


def normalize_daily_rows(rows):
    """Normalize OpenD daily rows to ``{'date', 'open', 'high', 'low', 'close'}``."""
    out = []
    for row in rows:
        if not hasattr(row, 'get'):
            continue
        day = _day(row.get('time_key'))
        if day is None:
            continue
        values = {key: _num(row.get(key)) for key in ('open', 'high', 'low', 'close')}
        if any(values[key] is None or values[key] <= 0 for key in values):
            continue
        out.append({'date': day, **values})
    dedup = {bar['date']: bar for bar in out}
    return [dedup[key] for key in sorted(dedup)]

