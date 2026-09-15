"""Option-only PnL for the custody product (the single sanctioned custody metric).

Locked custody model
--------------------
1. API **input**: the caller names an exact option contract to trade (nearest
   heavy-theta expiry is typical; near-ATM / not deep OTM; need not be strict
   0DTE).
2. **Timing / signals**: the program watches the same-day **underlying 1m**
   K-line for entry/exit timing.
3. **Execution**: when timing fires, the traded instrument is **that option**,
   never the stock.
4. **Success metric**: the option path / fills only. An underlying-proxy return
   (``underlying return * multiplier``) is **forbidden** as custody PnL.

This module is deliberately the only custody payoff path. The underlying-proxy
helper exists solely to raise, so a caller can never silently swap the option
metric for a stock proxy. The underlying-proxy research dataset
(``eval-data-v2``) is research leftover and must not flow through here.
"""
from __future__ import annotations

import math
from typing import Any

# US equity option multiplier (deliverable multiplier, not the orderable lot).
OPTION_MULTIPLIER = 100

# Roles that are allowed to produce a custody metric.
CUSTODY_ROLES = frozenset({
    'eval/custody',
    'validation/custody',
    'validation/eval',
    'train/custody',
})

# Roles that describe underlying-proxy research, never custody PnL.
FORBIDDEN_ROLES = frozenset({
    'train/research',
    'eval/research',
    'research',
    'underlying_proxy',
})


class CustodyMetricError(ValueError):
    """A custody metric was requested from a non-custody / underlying-only path."""


def _price(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(name + ' must be a non-negative finite number')
    return float(value)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(name + ' must be a positive integer')
    return value


def option_pnl(entry_price, exit_price, qty: int = 1, multiplier: int = OPTION_MULTIPLIER,
               direction: str | None = None) -> float:
    """Return option-contract PnL in USD for one round trip.

    Both product directions **buy** an option (LONG buys CALL, SHORT buys PUT)
    and close by selling the owned option, so the payoff is always
    ``(exit - entry) * qty * multiplier``. The product never writes/shorts an
    option; ``direction`` is accepted only for validation and labelling.
    """
    entry = _price(entry_price, 'entry_price')
    exit_ = _price(exit_price, 'exit_price')
    qty = _positive_int(qty, 'qty')
    multiplier = _positive_int(multiplier, 'multiplier')
    if direction is not None and direction not in ('LONG', 'SHORT'):
        raise ValueError('direction must be LONG or SHORT')
    return (exit_ - entry) * qty * multiplier


def option_return(entry_price, exit_price, qty: int = 1, multiplier: int = OPTION_MULTIPLIER,
                  direction: str | None = None) -> float:
    """Option-only return on premium paid (``option_pnl / entry cost``)."""
    entry = _price(entry_price, 'entry_price')
    qty = _positive_int(qty, 'qty')
    multiplier = _positive_int(multiplier, 'multiplier')
    if entry == 0:
        raise ValueError('entry_price must be positive to compute an option return')
    return option_pnl(entry, exit_price, qty, multiplier, direction) / (entry * qty * multiplier)


def _fill_price(fill):
    if fill is None:
        return None
    if isinstance(fill, dict):
        return fill.get('price')
    return getattr(fill, 'price', None)


def _fill_basis(fill):
    if fill is None:
        return None
    if isinstance(fill, dict):
        return fill.get('basis')
    return getattr(fill, 'basis', None)


def custody_case_pnl(entry, exit_, qty: int = 1, multiplier: int = OPTION_MULTIPLIER,
                     direction: str | None = None) -> dict:
    """Option-only PnL block for one custody case (``entry``/``exit`` fills).

    ``entry`` and ``exit_`` are fill objects/dicts carrying a real option
    ``price`` and a ``basis`` tag. This never consults an underlying price.
    """
    entry_price = _fill_price(entry)
    exit_price = _fill_price(exit_)
    block = {
        'success_metric': 'option_pnl',
        'metric_asset': 'option_contract',
        'entry_price': entry_price,
        'exit_price': exit_price,
        'qty': qty,
        'multiplier': multiplier,
        'direction': direction,
        'basis': _fill_basis(exit_) or _fill_basis(entry),
        'option_pnl': None,
        'option_return': None,
    }
    if entry_price is None or exit_price is None:
        return block
    block['option_pnl'] = option_pnl(entry_price, exit_price, qty, multiplier, direction)
    if entry_price > 0:
        block['option_return'] = option_return(entry_price, exit_price, qty, multiplier, direction)
    return block


def assert_custody_role(role) -> str:
    """Reject any dataset role that is not a paired custody dataset.

    ``eval-data-v2`` (underlying proxy research) or a missing/unknown role can
    never drive a custody metric.
    """
    if role in CUSTODY_ROLES:
        return role
    if role in FORBIDDEN_ROLES:
        raise CustodyMetricError(
            'custody PnL is option-only; dataset role %r is underlying-proxy research, not custody' % (role,))
    raise CustodyMetricError(
        'custody PnL requires a paired custody dataset role (one of %s); got %r'
        % (sorted(CUSTODY_ROLES), role))


def underlying_proxy_pnl(*args, **kwargs):
    """Hard block: an underlying-proxy payoff is never custody PnL."""
    raise CustodyMetricError(
        'forbidden custody metric: underlying-proxy payoff / underlying return x multiplier '
        'is not option PnL; measure the option contract path/fills instead')
