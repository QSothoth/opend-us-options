"""zero_dte_timing: scenario-adaptive timing for one bought 0DTE option per day.

Inputs are only the same-day completed underlying 1m bars and the direction chosen
upstream (LONG = the bought option is a CALL, SHORT = a PUT). All prices below are
*signed* by that direction, so "up" always means "in favour of the option".

There is one shot per contract per day, and the first breakout of the day is often
false. Entry requires confirmation; without a signal the engine stays flat.

Confirmed entry (pay premium only once the direction has proven itself)
------------------------------------------------------------------------
After the opening range (``opening_minutes``) enter on the first completed bar where

* the close is at least ``vwap_buffer_atr`` ATR on the favourable side of VWAP, and
  the last ``persist_minutes`` closes were all on that side (the move has held);
* the fast EMA is above the slow EMA (by ``trend_buffer_atr`` ATR) and still rising;
* the close breaks the highest signed high of the previous ``momentum_lookback`` bars.

Reason ``reversal_reclaim`` when the day first moved against us by more than the
opening-range height, otherwise ``trend_breakout``. Entry never loosens with the
clock (AGENTS.md: no fixed-time entry logic). With ``charm_exit_before_close_minutes``
there is no entry that the charm exit would sell on the next bar (out of the money
inside the charm window).

Optional entry filter (off by default): ``max_vwap_atr`` - no entry while the close is
more than this many ATR beyond VWAP. A buyer should not chase a move that has already
run away from VWAP: a stop of at most ``stop_max_atr`` ATR would then sit above VWAP,
inside an ordinary pullback, and the premium already pays for the finished move.
``min_breakout_volume_ratio`` requires current volume to reach a multiple of the
prior breakout window's mean volume. It defaults to off.

Optional exit rules (they need the contract strike)
---------------------------------------------------
* ``charm_exit_before_close_minutes``: that close to the close, an out-of-the-money
  position that is not yet protected by a trail is sold (``charm_exit``);
* ``protection_needs_moneyness`` = 1: the trail only counts as protection once the
  option is at or in the money - underlying progress measured in ATR says little about
  an option that is still far from its strike;
* ``otm_stop_shrink``: an entry already out of the money gets a proportionally tighter
  stop (factor ``max(0.5, 1 - shrink * z)``) because each ATR costs it a larger share of premium;
* ``take_profit_premium``: sell once the estimated premium return reaches the target
  (``take_profit``). The premium is estimated with the Bachelier model from the signed
  moneyness, the session sigma frozen at the entry decision and the minutes to the close
  (never from option quotes);
* ``min_premium_atr``: no entry while the estimated premium is below this many 1m ATR.
  Each fill gives up part of the option bar range, which is roughly a fixed share of an
  underlying ATR, so a small premium loses most of its return to friction.

Exits (cut losers early, let winners run)
-----------------------------------------
Risk is measured in the 1m ATR frozen at the entry decision.

* stop: confirmed entries below the recent swing low, clamped to
  [``stop_min_atr``, ``stop_max_atr``] ATR;
* no progress: after ``fail_minutes`` without
  ``fail_progress_atr`` ATR of progress and back at/below the entry mark;
* breakeven: after ``breakeven_at_atr`` ATR of progress the stop moves to the entry mark;
* trailing: after ``trail_activate_atr`` ATR the stop trails the best close by
  ``trail_atr`` ATR;
* flatten ``flatten_before_close_minutes`` before the session close.

All stops are evaluated on completed 1m closes of the underlying.
"""
import math

from ..indicators import SessionIndicators, bachelier
from ..strategy import Decision, flatten_minute, session_minute

SCHEMA = {
    'ema_fast': (int, 2, 50),
    'ema_slow': (int, 3, 200),
    'atr_period': (int, 2, 60),
    'opening_minutes': (int, 1, 60),
    'momentum_lookback': (int, 1, 30),
    'vwap_buffer_atr': (float, 0.0, 5.0),
    'trend_buffer_atr': (float, 0.0, 5.0),
    'stop_lookback': (int, 1, 60),
    'stop_min_atr': (float, 0.1, 10.0),
    'stop_max_atr': (float, 0.1, 20.0),
    'fail_minutes': (int, 1, 390),
    'fail_progress_atr': (float, 0.0, 50.0),
    'breakeven_at_atr': (float, 0.0, 50.0),
    'trail_activate_atr': (float, 0.0, 50.0),
    'trail_atr': (float, 0.1, 50.0),
    'flatten_before_close_minutes': (int, 15, 120),
}
# Optional signal and exit parameters.
OPTIONAL = {
    'persist_minutes': ((int, 0, 120), 0),          # confirmation needs N consecutive closes on the VWAP side
    'relax_after_minutes': ((int, 0, 0), 0),        # retired clock-based entry relaxation; only 0
    'charm_exit_before_close_minutes': ((int, 16, 389), None),  # late exit for unprotected out-of-the-money positions
    'protection_needs_moneyness': ((int, 0, 1), 0),  # 1: a trail only protects once the option is at/in the money
    'otm_stop_shrink': ((float, 0.05, 0.9), None),   # stop distance x max(0.5, 1 - shrink * OTM z at entry)
    'take_profit_premium': ((float, 0.05, 20.0), None),     # sell once the estimated premium return reaches this
    'min_premium_atr': ((float, 0.1, 100.0), None),  # no entry while the estimated premium is below N x 1m ATR
    'max_vwap_atr': ((float, 0.1, 20.0), None),      # no entry while the close is more than N ATR beyond VWAP
    'min_breakout_volume_ratio': ((float, 0.1, 5.0), None),  # current volume / prior breakout window mean
}
OTM_STOP_FLOOR = 0.5


def validate_params(params):
    if not isinstance(params, dict):
        raise ValueError('params must be an object')
    unknown, missing = set(params) - set(SCHEMA) - set(OPTIONAL), set(SCHEMA) - set(params)
    if unknown or missing:
        raise ValueError('params mismatch: unknown=%s missing=%s' % (sorted(unknown), sorted(missing)))
    out = {}
    for name, (kind, low, high) in SCHEMA.items():
        value = params[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(name + ' must be a number')
        if kind is int and value != int(value):
            raise ValueError(name + ' must be an integer')
        if not low <= value <= high:
            raise ValueError('%s must be within [%s, %s]' % (name, low, high))
        out[name] = kind(value)
    for name, ((kind, low, high), default) in OPTIONAL.items():
        value = params.get(name, default)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind is int and value != int(value)):
                raise ValueError(name + ' must be a number of the right kind')
            if not low <= value <= high:
                raise ValueError('%s must be within [%s, %s]' % (name, low, high))
            value = kind(value)
        out[name] = value
    if out['ema_fast'] >= out['ema_slow'] or out['stop_min_atr'] > out['stop_max_atr']:
        raise ValueError('inconsistent EMA or stop parameters')
    if out['max_vwap_atr'] is not None and out['max_vwap_atr'] <= out['vwap_buffer_atr']:
        raise ValueError('max_vwap_atr must exceed vwap_buffer_atr')
    return out


class ZeroDteTiming:
    validate = staticmethod(validate_params)

    def __init__(self, params, direction, session, strike):
        self.p = validate_params(params)
        if isinstance(strike, bool) or not isinstance(strike, (int, float)) or not strike > 0:
            raise ValueError('strike must be positive')
        self.strike = strike
        if direction not in ('LONG', 'SHORT'):
            raise ValueError('direction must be LONG or SHORT')
        self.sign = 1 if direction == 'LONG' else -1
        self.session = session
        self.flatten_minute = flatten_minute(self.p, session)
        self.session_minutes = int((session.closes - session.opens).total_seconds() // 60)
        self.ind = SessionIndicators(self.p['ema_fast'], self.p['ema_slow'], self.p['atr_period'],
                                     self.p['opening_minutes'],
                                     history=max(self.p['momentum_lookback'], self.p['stop_lookback']) + 2)
        self.phase = 'FLAT'            # FLAT -> ENTERING -> IN -> EXITING
        self.vwap_side_bars = 0        # consecutive closes on the favourable side of VWAP
        self.entry_reason = self.exit_reason = None
        self.risk_distance = self.entry_atr = None
        self.entry_mark = self.entry_minute = self.stop = self.best = None
        self.sigma = self.entry_value = None

    # ------------------------------------------------------------ helpers
    def _hi(self, bar):
        return max(self.sign * bar.high, self.sign * bar.low)

    def _lo(self, bar):
        return min(self.sign * bar.high, self.sign * bar.low)

    def _diagnostics(self, minute):
        ind = self.ind
        return {'minute': minute, 'close': ind.close, 'vwap': round(ind.vwap, 6), 'atr': round(ind.atr, 6),
                'ema_fast': round(ind.ema_fast, 6), 'ema_slow': round(ind.ema_slow, 6), 'phase': self.phase,
                'stop': None if self.stop is None else round(self.sign * self.stop, 6),
                'progress_atr': None if self.best is None else round((self.best - self.entry_mark) / self.entry_atr, 3)}

    # ------------------------------------------------------------ contract
    def on_bar(self, bar):
        minute = session_minute(self.session, bar.close_time)
        self.ind.update(bar, minute)
        self.vwap_side_bars = self.vwap_side_bars + 1 if self.sign * (bar.close - self.ind.vwap) > 0 else 0
        if self.phase != 'FLAT' and minute >= self.flatten_minute:
            if self.phase != 'EXITING':
                self.phase, self.exit_reason = 'EXITING', 'scheduled_flatten'
            return Decision('EXIT', self.exit_reason, self._diagnostics(minute))
        if self.phase == 'EXITING':
            return Decision('EXIT', self.exit_reason, self._diagnostics(minute))
        if self.phase == 'ENTERING':
            return Decision('ENTER', self.entry_reason, self._diagnostics(minute))
        if self.phase == 'IN':
            return self._manage(minute)
        if minute >= self.flatten_minute:
            return Decision('WAIT', None, self._diagnostics(minute))
        reason = self._entry_reason(bar, minute)
        if reason is None:
            return Decision('WAIT', None, self._diagnostics(minute))
        self.phase, self.entry_reason = 'ENTERING', reason
        self._plan_risk()
        return Decision('ENTER', reason, self._diagnostics(minute))

    def _otm_z(self, minute):
        """How far out of the money, in expected remaining moves (1m ATR x sqrt(minutes left))."""
        left = max(self.session_minutes - minute, 1)
        distance = -self.sign * (self.ind.close - self.strike)
        return distance / (self.ind.atr * left ** 0.5)

    def _premium(self, signed_price, minute, sigma=None):
        left = max(self.session_minutes - minute, 1)
        return bachelier(signed_price - self.sign * self.strike, (sigma or self.sigma) * math.sqrt(left))

    def _in_charm_window(self, minute):
        charm = self.p['charm_exit_before_close_minutes']
        return charm is not None and minute >= self.session_minutes - charm and self._otm_z(minute) > 0

    def _plan_risk(self):
        """Freeze ATR, sigma and the stop distance from the latest completed bar."""
        self.entry_atr, self.sigma = self.ind.atr, self.ind.sigma
        recent = self.ind.prior_bars(self.p['stop_lookback']) + [self.ind.bars[-1]]
        distance = self.sign * self.ind.close - min(self._lo(b) for b in recent)
        self.risk_distance = min(max(distance, self.p['stop_min_atr'] * self.entry_atr),
                                 self.p['stop_max_atr'] * self.entry_atr)
        if self.p['otm_stop_shrink'] is not None:
            z = max(0.0, self._otm_z(self.ind.minute))
            self.risk_distance *= max(OTM_STOP_FLOOR, 1 - self.p['otm_stop_shrink'] * z)

    def on_entry_filled(self, at, underlying_mark):
        if self.phase != 'ENTERING':
            raise ValueError('entry fill reported without a pending entry decision')
        self.phase = 'IN'
        self.entry_minute = (at - self.session.opens).total_seconds() / 60
        self.entry_mark = self.best = self.sign * float(underlying_mark)
        self.stop = self.entry_mark - self.risk_distance
        self.entry_value = max(self._premium(self.entry_mark, self.entry_minute), self.sigma * 1e-3)

    # ------------------------------------------------------------ rules
    def _entry_reason(self, bar, minute):
        p, ind, s = self.p, self.ind, self.sign
        if minute < p['opening_minutes'] or ind.prev_ema_fast is None:
            return None
        atr, close = ind.atr, s * bar.close
        prior = ind.prior_bars(p['momentum_lookback'])
        if len(prior) < p['momentum_lookback'] or close <= max(self._hi(b) for b in prior):
            return None
        if p['min_breakout_volume_ratio'] is not None:
            mean_volume = sum(b.volume for b in prior) / len(prior)
            if mean_volume <= 0 or bar.volume <= 0 or bar.volume < p['min_breakout_volume_ratio'] * mean_volume:
                return None
        if self.vwap_side_bars < p['persist_minutes']:
            return None
        vwap_side = close >= s * ind.vwap + p['vwap_buffer_atr'] * atr
        trend = (s * (ind.ema_fast - ind.ema_slow) >= p['trend_buffer_atr'] * atr
                 and s * (ind.ema_fast - ind.prev_ema_fast) > 0)
        if not (vwap_side and trend):
            return None
        if p['max_vwap_atr'] is not None and close > s * ind.vwap + p['max_vwap_atr'] * atr:
            return None
        if self._in_charm_window(minute):
            return None
        if p['min_premium_atr'] is not None and self._premium(close, minute, ind.sigma) < p['min_premium_atr'] * atr:
            return None
        if s * ind.session_open - min(s * ind.high, s * ind.low) > ind.or_high - ind.or_low:
            return 'reversal_reclaim'
        return 'trend_breakout'

    def _manage(self, minute):
        p, close = self.p, self.sign * self.ind.close
        self.best = max(self.best, close)
        progress = (self.best - self.entry_mark) / self.entry_atr
        reason = 'invalidation_stop'
        if progress >= p['breakeven_at_atr']:
            self.stop = max(self.stop, self.entry_mark)
            reason = 'breakeven_stop'
        if progress >= p['trail_activate_atr']:
            self.stop = max(self.stop, self.best - p['trail_atr'] * self.entry_atr)
            reason = 'trailing_stop'
        premium_return = self._premium(close, minute) / self.entry_value - 1
        protected = progress >= p['trail_activate_atr']
        if protected and p['protection_needs_moneyness'] and self._otm_z(minute) > 0:
            protected = False  # underlying progress in ATR means little while the option is still out of the money
        if close <= self.stop:
            self.phase, self.exit_reason = 'EXITING', reason
        elif p['take_profit_premium'] is not None and premium_return >= p['take_profit_premium']:
            self.phase, self.exit_reason = 'EXITING', 'take_profit'
        elif not protected and self._in_charm_window(minute):
            self.phase, self.exit_reason = 'EXITING', 'charm_exit'
        elif (minute - self.entry_minute >= p['fail_minutes'] and progress < p['fail_progress_atr']
              and close <= self.entry_mark):
            self.phase, self.exit_reason = 'EXITING', 'no_progress'
        if self.phase == 'EXITING':
            return Decision('EXIT', self.exit_reason, self._diagnostics(minute))
        return Decision('HOLD', None, self._diagnostics(minute))
