"""zero_dte_timing: scenario-adaptive timing for one bought 0DTE option per day.

Inputs are only the same-day completed underlying 1m bars and the direction chosen
upstream (LONG = the bought option is a CALL, SHORT = a PUT). All prices below are
*signed* by that direction, so "up" always means "in favour of the option".

There is one shot per contract per day, and the first breakout of the day is often
false. The engine therefore separates two kinds of trades:

Confirmed entry (pay premium only once the direction has proven itself)
------------------------------------------------------------------------
After the opening range (``opening_minutes``) enter on the first completed bar where

* the close is at least ``vwap_buffer_atr`` ATR on the favourable side of VWAP, and
  the last ``persist_minutes`` closes were all on that side (the move has held);
* the fast EMA is above the slow EMA (by ``trend_buffer_atr`` ATR) and still rising;
* the close breaks the highest signed high of the previous ``momentum_lookback`` bars.

Reason ``reversal_reclaim`` when the day first moved against us by more than the
opening-range height, otherwise ``trend_breakout``. From ``relax_after_minutes``
(0 disables) a fresh breakout on either the VWAP side or the EMA trend is enough
(``late_confirmation``).

Forced entry (must-trade, the direction never confirmed)
--------------------------------------------------------
At ``must_enter_before_close_minutes`` the engine enters unconditionally
(``must_trade_deadline``). Such a trade gets its own tight risk when configured:
a fixed ``forced_stop_atr`` stop and a ``forced_fail_minutes`` no-progress clock.

Exits (cut losers early, let winners run)
-----------------------------------------
Risk is measured in the 1m ATR frozen at the entry decision.

* stop: confirmed entries below the recent swing low, clamped to
  [``stop_min_atr``, ``stop_max_atr``] ATR; forced entries ``forced_stop_atr`` ATR;
* no progress: after ``fail_minutes`` (forced: ``forced_fail_minutes``) without
  ``fail_progress_atr`` ATR of progress and back at/below the entry mark;
* breakeven: after ``breakeven_at_atr`` ATR of progress the stop moves to the entry mark;
* trailing: after ``trail_activate_atr`` ATR the stop trails the best close by
  ``trail_atr`` ATR; there is no profit target;
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
    'relax_after_minutes': (int, 0, 390),
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
# Optional parameters (absent = the v1 behaviour). None means "same as the normal rule".
OPTIONAL = {
    'persist_minutes': ((int, 0, 120), 0),          # confirmation needs N consecutive closes on the VWAP side
    'forced_stop_atr': ((float, 0.1, 10.0), None),  # fixed stop for entries that were never confirmed
    'forced_fail_minutes': ((int, 1, 390), None),   # no-progress clock for never-confirmed entries
}


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
    if out['relax_after_minutes'] and out['relax_after_minutes'] <= out['opening_minutes']:
        raise ValueError('relax_after_minutes must follow the opening range (or be 0 to disable)')
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
        if self.p['relax_after_minutes'] and self.p['relax_after_minutes'] >= self.must_enter_minute:
            raise ValueError('relax_after_minutes must precede the must-enter deadline')
        self.ind = SessionIndicators(self.p['ema_fast'], self.p['ema_slow'], self.p['atr_period'],
                                     self.p['opening_minutes'],
                                     history=max(self.p['momentum_lookback'], self.p['stop_lookback']) + 2)
        self.phase = 'FLAT'            # FLAT -> ENTERING -> IN -> EXITING
        self.vwap_side_bars = 0        # consecutive closes on the favourable side of VWAP
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
        reason = self._entry_reason(bar, minute)
        if reason is None:
            return Decision('WAIT', None, self._diagnostics(minute))
        self.phase, self.entry_reason = 'ENTERING', reason
        self._plan_risk()
        return Decision('ENTER', reason, self._diagnostics(minute))

    @property
    def forced(self):
        return self.entry_reason in ('must_trade_deadline', 'platform_entry')

    def _plan_risk(self):
        """Freeze ATR and the stop distance from the latest completed bar."""
        self.entry_atr = self.ind.atr
        if self.forced and self.p['forced_stop_atr'] is not None:
            self.risk_distance = self.p['forced_stop_atr'] * self.entry_atr
            return
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
        if self.vwap_side_bars < p['persist_minutes']:
            return None
        vwap_side = close >= s * ind.vwap + p['vwap_buffer_atr'] * atr
        trend = (s * (ind.ema_fast - ind.ema_slow) >= p['trend_buffer_atr'] * atr
                 and s * (ind.ema_fast - ind.prev_ema_fast) > 0)
        if vwap_side and trend:
            opening_range = ind.or_high - ind.or_low
            if s * ind.session_open - min(s * ind.high, s * ind.low) > opening_range:
                return 'reversal_reclaim'
            return 'trend_breakout'
        if p['relax_after_minutes'] and minute >= p['relax_after_minutes'] and (vwap_side or trend):
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
        elif (minute - self.entry_minute >= self._fail_minutes() and progress < p['fail_progress_atr']
              and close <= self.entry_mark):
            self.phase, self.exit_reason = 'EXITING', 'no_progress'
        if self.phase == 'EXITING':
            return Decision('EXIT', self.exit_reason, self._diagnostics(minute))
        return Decision('HOLD', None, self._diagnostics(minute))

    def _fail_minutes(self):
        if self.forced and self.p['forced_fail_minutes'] is not None:
            return self.p['forced_fail_minutes']
        return self.p['fail_minutes']
