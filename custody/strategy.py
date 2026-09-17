"""The timing-strategy contract shared by evaluation, dryrun and the service.

A timing strategy answers one question per completed underlying 1m bar: given the
direction and contract chosen upstream, should the single daily trade enter now,
keep holding, or exit now? It never chooses the direction or the contract, never
sees option prices, previous sessions or daily bars, and must be deterministic.

Lifecycle for one (contract, session)::

    engine = build_strategy(item, direction, session, strike)
    for bar in same_day_underlying_1m_bars:        # completed bars, in order
        if entry filled at or before bar.close_time:
            engine.on_entry_filled(fill_time, underlying_mark)   # exactly once
        decision = engine.on_bar(bar)

Actions:

* ``WAIT``  - flat, keep watching;
* ``ENTER`` - buy now (repeated until the fill is reported);
* ``HOLD``  - in position (or entry pending), keep holding;
* ``EXIT``  - sell now (repeated until the platform finishes the exit).

Completion is a soft evaluation score: WAIT all day is valid. The platform never
forces entry. Existing positions must EXIT by ``flatten_before_close_minutes``.
"""
from dataclasses import dataclass, field
from datetime import datetime

from .models import Session, instant

ACTIONS = ('WAIT', 'ENTER', 'HOLD', 'EXIT')
PLATFORM_MIN_FLATTEN_MINUTES = 15


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str | None = None
    diagnostics: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.action not in ACTIONS:
            raise ValueError('unknown strategy action: %r' % (self.action,))


def session_minute(session: Session, when: datetime) -> int:
    """Whole minutes from the session open to a completed bar close (1 = first bar)."""
    seconds = (instant(when) - session.opens).total_seconds()
    if seconds <= 0 or seconds % 60 or instant(when) > session.closes:
        raise ValueError('bar close is not a completed regular-session minute')
    return int(seconds // 60)


def session_length(session: Session) -> int:
    return int((session.closes - session.opens).total_seconds() // 60)


def flatten_minute(params: dict, session: Session) -> int:
    """Last entry boundary and mandatory exit decision, in minutes from the open."""
    minute = session_length(session) - max(int(params['flatten_before_close_minutes']), PLATFORM_MIN_FLATTEN_MINUTES)
    if minute <= 0:
        raise ValueError('flatten deadline does not fit this session')
    return minute


def engines():
    from .engines import ENGINES
    return ENGINES


def build_strategy(item: dict, direction: str, session: Session, strike: float):
    """Instantiate the registered engine for one job/case (strike of the exact contract)."""
    if direction not in ('LONG', 'SHORT'):
        raise ValueError('direction must be LONG or SHORT')
    config = item['config']
    engine = engines().get(config['engine'])
    if engine is None:
        raise ValueError('unknown strategy engine: %r' % (config['engine'],))
    return engine(config['params'], direction, session, strike)
