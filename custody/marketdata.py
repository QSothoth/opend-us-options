"""Shared market-data boundary for the custody offline eval and live OpenD paths.

Both the frozen-slice provider (:mod:`custody.offline`) and the live read-only
OpenD adapter (:class:`custody.opend.OpenDMarket`) return the same :class:`Bar`
records and :class:`~custody.models.Quote` objects. The custody controller and
eval harness are therefore provider-agnostic: moving from a frozen slice to
OpenD is a **provider swap**, not a controller rewrite.

Honest live/offline split
-------------------------
* 1-minute history is trade OHLCV (``open/high/low/close/volume``) for both the
  underlying and the option, offline and live.
* Option **bid/ask** only exists live (OpenD snapshot/quote). The frozen slice
  does not contain NBBO, so :class:`~custody.offline.OfflineMarket` never
  fabricates a spread: ``quote`` returns ``None`` unless the caller explicitly
  opts into the clearly-labelled ``option_bar_close`` mapping used by the
  must-trade plumbing stub. Live uses real quotes.

This module intentionally has no network client and no order API.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Protocol, runtime_checkable

from .models import Quote, ET, instant

__all__ = ['Bar', 'MarketDataProvider', 'MissingCustodyPairError', 'normalize_bar_rows',
           'normalize_daily_rows', 'day_range', 'day_bars', 'require_paired_bars']


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

    def to_record(self):
        """Feature-worker / :class:`custody.signals.SignalProvider` record shape."""
        return {'close_time': self.close_time.isoformat(), 'open': self.open, 'high': self.high,
                'low': self.low, 'close': self.close, 'volume': self.volume}


def normalize_bar_rows(rows, code, interval='1m', boundary=None, source='opend_kline'):
    """Normalize raw OpenD ``request_history_kline`` rows into deduped ``Bar``s.

    Rows with missing/non-finite values, a malformed timestamp or an impossible
    OHLC range are dropped. ``boundary`` (an aware datetime or ISO string)
    filters out anything after the requested bar close. Output is ordered by
    close time and de-duplicated by close time.
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


def day_range(day):
    """Return ``(start, end)`` OpenD history strings covering one ET calendar day."""
    if isinstance(day, date):
        day = day.isoformat()
    else:
        day = date.fromisoformat(str(day)).isoformat()
    return day + ' 00:00:00', day + ' 23:59:59'


def day_bars(provider, code, day, ktype='K_1M', boundary=None):
    """Provider-agnostic single-session fetch used by fetch and eval paths."""
    start, end = day_range(day)
    return provider.history_bars(code, ktype, start, end, boundary=boundary)


class MissingCustodyPairError(ValueError):
    """A custody case needs both the same-day underlying and option series."""


def require_paired_bars(provider, symbol, contract, day, ktype='K_1M', boundary=None):
    """Load a custody case's paired same-day underlying + option bars or fail.

    Custody timing watches the underlying 1m, but the traded and measured asset
    is the option. A case that silently lacks one side (for example an
    underlying-only research slice) must never be evaluated as custody.
    """
    try:
        underlying = day_bars(provider, symbol, day, ktype=ktype, boundary=boundary)
    except KeyError:
        underlying = []
    try:
        option = day_bars(provider, contract, day, ktype=ktype, boundary=boundary)
    except KeyError:
        option = []
    missing = []
    if not underlying:
        missing.append('underlying ' + str(symbol))
    if not option:
        missing.append('option ' + str(contract))
    if missing:
        raise MissingCustodyPairError(
            'custody case %s/%s on %s requires paired underlying + option bars; missing %s'
            % (symbol, contract, day, ', '.join(missing)))
    return underlying, option


@runtime_checkable
class MarketDataProvider(Protocol):
    """The single boundary shared by the frozen-slice and live OpenD providers."""

    def quote(self, contract: str, now=None) -> Quote | None:
        """Fresh option bid/ask, or ``None`` when no honest quote is available."""
        ...

    def underlying_mark(self, symbol: str, now=None) -> float | None:
        """Fresh underlying mark, or ``None`` when unavailable."""
        ...

    def history_bars(self, code: str, ktype: str, start, end, boundary=None) -> list[Bar]:
        """Completed history bars for ``code`` in ``[start, end]``."""
        ...

    def current_bars(self, code: str, count: int, ktype: str, boundary=None) -> list[Bar]:
        """Most recent ``count`` bars (subscribed stream or frozen tail)."""
        ...
