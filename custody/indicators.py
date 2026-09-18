"""Causal same-session indicators over completed underlying 1m bars.

Standard library only. Every value available after ``update(bar)`` depends only on
bars already passed in, never on a later bar, a previous session, a daily bar or an
option price. Timing engines (:mod:`custody.engines`) build on this class and apply
the direction sign themselves.
"""
import math
from collections import deque


def bachelier(moneyness, spread):
    """Undiscounted option value for signed moneyness and total standard deviation of the move."""
    if spread <= 0:
        return max(moneyness, 0.0)
    d = moneyness / spread
    return moneyness * 0.5 * (1 + math.erf(d / math.sqrt(2))) + spread * math.exp(-d * d / 2) / math.sqrt(2 * math.pi)


class SessionIndicators:
    """Incremental VWAP / EMA / ATR / sigma / opening range / extremes for one session."""

    def __init__(self, ema_fast=9, ema_slow=21, atr_period=14, opening_minutes=15, history=60):
        if not 1 <= ema_fast < ema_slow or atr_period < 1 or opening_minutes < 1 or history < 2:
            raise ValueError('invalid indicator parameters')
        self.ema_fast_period, self.ema_slow_period = ema_fast, ema_slow
        self.opening_minutes = opening_minutes
        self.bars = deque(maxlen=history)
        self.true_ranges = deque(maxlen=atr_period)
        self.count = 0
        self.minute = 0
        self.session_open = None
        self.high = self.low = None
        self.or_high = self.or_low = None
        self.ema_fast = self.ema_slow = None
        self.prev_ema_fast = None
        self._pv = self._volume = 0.0
        self.vwap = None
        self.atr = None
        self._squares = 0.0

    @property
    def close(self):
        return self.bars[-1].close

    @property
    def or_ready(self):
        return self.minute >= self.opening_minutes

    def update(self, bar, minute):
        """Add the completed bar whose close is ``minute`` minutes after the open."""
        if minute <= self.minute:
            raise ValueError('bars must arrive in increasing minute order')
        prev = self.bars[-1] if self.bars else None
        if prev is None:
            self.session_open = bar.open
            true_range = bar.high - bar.low
        else:
            true_range = max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close))
            self._squares += (bar.close - prev.close) ** 2
        self.true_ranges.append(true_range)
        self.atr = max(sum(self.true_ranges) / len(self.true_ranges), bar.close * 1e-5)
        self.high = bar.high if self.high is None else max(self.high, bar.high)
        self.low = bar.low if self.low is None else min(self.low, bar.low)
        if minute <= self.opening_minutes:
            self.or_high = bar.high if self.or_high is None else max(self.or_high, bar.high)
            self.or_low = bar.low if self.or_low is None else min(self.or_low, bar.low)
        self.prev_ema_fast = self.ema_fast
        if self.ema_fast is None:
            self.ema_fast = self.ema_slow = bar.close
        else:
            self.ema_fast += 2 / (self.ema_fast_period + 1) * (bar.close - self.ema_fast)
            self.ema_slow += 2 / (self.ema_slow_period + 1) * (bar.close - self.ema_slow)
        self._pv += (bar.high + bar.low + bar.close) / 3 * bar.volume
        self._volume += bar.volume
        self.vwap = self._pv / self._volume if self._volume > 0 else bar.close
        self.bars.append(bar)
        self.count += 1
        self.minute = minute

    @property
    def sigma(self):
        """Root mean square of this session's completed 1m close-to-close changes."""
        changes = self.count - 1
        return max(math.sqrt(self._squares / changes) if changes > 0 else 0.0, self.close * 1e-5)

    def prior_bars(self, n):
        """The ``n`` bars before the latest one (the latest bar is excluded)."""
        items = list(self.bars)[:-1]
        return items[-n:] if n > 0 else []
