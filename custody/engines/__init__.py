"""Timing engines by name. A registered strategy JSON selects one engine plus params.

To add a strategy: implement the :mod:`custody.strategy` contract in a new module,
add it here, then register an immutable JSON under ``custody/strategies/``.
"""
from .zero_dte_timing import ZeroDteTiming

ENGINES = {
    'zero_dte_timing': ZeroDteTiming,
}
