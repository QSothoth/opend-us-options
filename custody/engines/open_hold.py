"""open_hold: no timing at all - buy at a fixed minute after the open, hold to the flatten.

This is the executable form of the evaluation benchmark, added on 2026-09-20 by user
decision (AGENTS.md) for the case where the upstream direction call is accurate enough
that timing costs more than it saves. It deliberately breaks the "entry does not depend
on a fixed time" rule that applies to timing engines, so it is registered as a separate
engine and judged by its own break-even hit rate rather than against the benchmark.

Measured on custody-0dte-v6.1 + custody-eval-2026-09-18-v2 (174 cases), buying at 09:35
and holding returns +45.7% when the direction is right and -93.2% when it is wrong, which
breaks even at a 73.3% upstream hit rate. Adding stop rules to a fixed-time entry was
tested and is worse at every hit rate below 95% (docs/STRATEGY.md, R21), so this engine
holds: between the entry and the flatten it makes no decisions.

Parameters
----------
``entry_minute``      minutes after the open at which the buy decision is made (the fill
                      lands on the next option bar, as for any strategy).
``flatten_before_close_minutes``  platform-mandated exit, at least 15.
"""
from ..strategy import Decision, flatten_minute, session_minute

SCHEMA = {
    'entry_minute': (int, 1, 120),
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
    return out


class OpenHold:
    validate = staticmethod(validate_params)

    def __init__(self, params, direction, session, strike):
        self.p = validate_params(params)
        if isinstance(strike, bool) or not isinstance(strike, (int, float)) or not strike > 0:
            raise ValueError('strike must be positive')
        if direction not in ('LONG', 'SHORT'):
            raise ValueError('direction must be LONG or SHORT')
        self.session = session
        self.flatten_minute = flatten_minute(self.p, session)
        if self.p['entry_minute'] >= self.flatten_minute:
            raise ValueError('entry_minute must fall before the flatten deadline')
        self.phase = 'FLAT'

    def on_bar(self, bar):
        minute = session_minute(self.session, bar.close_time)
        if self.phase in ('IN', 'ENTERING') and minute >= self.flatten_minute:
            self.phase = 'EXITING'
        if self.phase == 'EXITING':
            return Decision('EXIT', 'scheduled_flatten', {'minute': minute, 'phase': self.phase})
        if self.phase == 'ENTERING':
            return Decision('ENTER', 'open_entry', {'minute': minute, 'phase': self.phase})
        if self.phase == 'IN':
            return Decision('HOLD', None, {'minute': minute, 'phase': self.phase})
        if minute >= self.p['entry_minute'] and minute < self.flatten_minute:
            self.phase = 'ENTERING'
            return Decision('ENTER', 'open_entry', {'minute': minute, 'phase': self.phase})
        return Decision('WAIT', None, {'minute': minute, 'phase': self.phase})

    def on_entry_filled(self, at, underlying_mark):
        if self.phase != 'ENTERING':
            raise ValueError('entry fill reported without a pending entry decision')
        self.phase = 'IN'
