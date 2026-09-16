"""zero_dte_timing: scenario-adaptive timing for one bought 0DTE option per day.

Inputs are only the same-day completed underlying 1m bars and the direction chosen
upstream (LONG = the bought option is a CALL, SHORT = a PUT). All prices below are
*signed* by that direction, so "up" always means "in favour of the option".

Entry (payoff first: pay premium only once the chosen direction is confirmed)
-----------------------------------------------------------------------------
No entry while the opening range forms. Afterwards, enter on the first completed
bar where all three hold:

* price is at least ``vwap_buffer_atr`` ATR on the favourable side of VWAP;
* fast EMA is above slow EMA (by ``trend_buffer_atr`` ATR) and still rising;
* the close breaks the highest signed high of the previous ``momentum_lookback`` bars.

This one rule adapts to the common day shapes instead of fixing a clock time:

* trend in our favour (e.g. gap then run): fires right after the opening range;
* early move against us, then reversal (低开高走 for a CALL): waits through the
  adverse leg and fires on the VWAP reclaim with fresh momentum;
* trend against us all day (高开低走 for a CALL): never confirms, so no premium is
  paid early; the must-trade deadline enters late and the stop / no-progress rules
  cut the position quickly;
* chop: requires VWAP side + EMA trend + a fresh breakout at once, which filters
  most whipsaws; what gets through is cut quickly by the stop or the no-progress rule.

After ``relax_after_minutes`` a fresh breakout on either the VWAP side or the EMA
trend is enough. At ``must_enter_before_close_minutes`` the engine enters
unconditionally (must-trade: the upstream selector already decided this trade).

Exit (cut losers early, let winners run)
----------------------------------------
Risk is measured in the 1m ATR frozen at the entry decision.

* invalidation stop: below the recent swing low, clamped to
  [``stop_min_atr``, ``stop_max_atr``] ATR from the entry mark;
* no progress: after ``fail_minutes`` without ``fail_progress_atr`` ATR of favourable
  progress and back at/below the entry mark, exit (0DTE theta punishes waiting);
* breakeven: after ``breakeven_at_atr`` ATR of progress the stop moves to the entry mark;
* trailing: after ``trail_activate_atr`` ATR the stop trails the best close by
  ``trail_atr`` ATR. There is no profit target;
* flatten ``flatten_before_close_minutes`` before the session close.

All stops are evaluated on completed 1m closes of the underlying.
"""
from ..indicators import SessionIndicators
from ..strategy import Decision, deadlines, session_minute

SCHEMA = {
    'ema_fast': (int, 2, 50),
    'ema_slow': (int, 3, 200),
    'atr_period': (int, 2, 60),
    'opening_minutes': (int, 1, 60),
    'momentum_lookback': (int, 1, 30),
    'vwap_buffer_atr': (float, 0.0, 5.0),
    'trend_buffer_atr': (float, 0.0, 5.0),
    'relax_after_minutes': (int, 1, 390),
    'must_enter_before_close_minutes': (int, 16, 389),
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


def validate_params(params):
    if not isinstance(params, dict):
        raise ValueError('params must be an object')
    unknown, missing = set(params) - set(SCHEMA), set(SCHEMA) - set(params)
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
    if out['ema_fast'] >= out['ema_slow'] or out['stop_min_atr'] > out['stop_max_atr']:
        raise ValueError('inconsistent EMA or stop parameters')
    if out['relax_after_minutes'] <= out['opening_minutes']:
        raise ValueError('relax_after_minutes must follow the opening range')
    return out


class ZeroDteTiming:
    validate = staticmethod(validate_params)

    def __init__(self, params, direction, session):
        self.p = validate_params(params)
        if direction not in ('LONG', 'SHORT'):
            raise ValueError('direction must be LONG or SHORT')
        self.sign = 1 if direction == 'LONG' else -1
        self.session = session
        self.must_enter_minute, self.flatten_minute = deadlines(self.p, session)
        if self.p['relax_after_minutes'] >= self.must_enter_minute:
            raise ValueError('relax_after_minutes must precede the must-enter deadline')
        self.ind = SessionIndicators(self.p['ema_fast'], self.p['ema_slow'], self.p['atr_period'],
                                     self.p['opening_minutes'],
                                     history=max(self.p['momentum_lookback'], self.p['stop_lookback']) + 2)
        self.phase = 'FLAT'            # FLAT -> ENTERING -> IN -> EXITING
        self.entry_reason = self.exit_reason = None
        self.risk_distance = self.entry_atr = None
        self.entry_mark = self.entry_minute = self.stop = self.best = None

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
        reason = self._entry_reason(bar, minute)
        if reason is None:
            return Decision('WAIT', None, self._diagnostics(minute))
        self.phase, self.entry_reason = 'ENTERING', reason
        self._plan_risk()
        return Decision('ENTER', reason, self._diagnostics(minute))

    def _plan_risk(self):
        """Freeze ATR and the stop distance from the latest completed bar."""
        self.entry_atr = self.ind.atr
        recent = self.ind.prior_bars(self.p['stop_lookback']) + [self.ind.bars[-1]]
        distance = self.sign * self.ind.close - min(self._lo(b) for b in recent)
        self.risk_distance = min(max(distance, self.p['stop_min_atr'] * self.entry_atr),
                                 self.p['stop_max_atr'] * self.entry_atr)

    def on_entry_filled(self, at, underlying_mark):
        if self.phase == 'FLAT' and self.ind.count:
            # The platform may force the must-trade entry between bars (e.g. a frame
            # outage); plan the risk from the latest completed bar.
            self.entry_reason = 'platform_entry'
            self._plan_risk()
        elif self.phase != 'ENTERING':
            raise ValueError('entry fill reported without a pending entry decision')
        self.phase = 'IN'
        self.entry_minute = (at - self.session.opens).total_seconds() / 60
        self.entry_mark = self.best = self.sign * float(underlying_mark)
        self.stop = self.entry_mark - self.risk_distance

    # ------------------------------------------------------------ rules
    def _entry_reason(self, bar, minute):
        p, ind, s = self.p, self.ind, self.sign
        if minute >= self.must_enter_minute:
            return 'must_trade_deadline'
        if minute < p['opening_minutes'] or ind.prev_ema_fast is None:
            return None
        atr, close = ind.atr, s * bar.close
        prior = ind.prior_bars(p['momentum_lookback'])
        if len(prior) < p['momentum_lookback'] or close <= max(self._hi(b) for b in prior):
            return None
        vwap_side = close >= s * ind.vwap + p['vwap_buffer_atr'] * atr
        trend = (s * (ind.ema_fast - ind.ema_slow) >= p['trend_buffer_atr'] * atr
                 and s * (ind.ema_fast - ind.prev_ema_fast) > 0)
        if vwap_side and trend:
            opening_range = ind.or_high - ind.or_low
            if s * ind.session_open - min(s * ind.high, s * ind.low) > opening_range:
                return 'reversal_reclaim'
            return 'trend_breakout'
        if minute >= p['relax_after_minutes'] and (vwap_side or trend):
            return 'late_confirmation'
        return None

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
        if close <= self.stop:
            self.phase, self.exit_reason = 'EXITING', reason
        elif (minute - self.entry_minute >= p['fail_minutes'] and progress < p['fail_progress_atr']
              and close <= self.entry_mark):
            self.phase, self.exit_reason = 'EXITING', 'no_progress'
        if self.phase == 'EXITING':
            return Decision('EXIT', self.exit_reason, self._diagnostics(minute))
        return Decision('HOLD', None, self._diagnostics(minute))
