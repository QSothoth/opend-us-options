"""OpenD order adapter for ``custody run``: ``paper`` = OpenD SIMULATE account, ``live`` = REAL money.

The only module that uses the OpenD trade API. It implements :class:`custody.ports.Broker`:

* every order carries the service's client order id in the OpenD ``remark``. Ids are
  deterministic per account/contract/day, so an order is found again after a timeout or a
  restart - even with a lost or different database - and is never placed twice;
* US limit orders only (``NORMAL``, DAY, regular hours); BUY opens, SELL only closes what
  the account holds (checked right before sending);
* a failed ``place_order`` that OpenD answered with an error, and whose id is not in the
  order list, raises :class:`HardSubmitError` so the service can mint a new client order id;
  a transport failure (timeout, disconnect: the order may still land) raises a plain
  exception so the service keeps the order UNKNOWN and reconciles via :meth:`lookup`.

Order state is polled; a fill is stamped with the poll that first sees it.
"""
from __future__ import annotations

import os
import re

from .dataset import parse_option_code
from .marketdata import _num
from .models import HardSubmitError, OrderUpdate, UnlockRequiredError, instant
from .opend import _futu, _records

MODES = {'paper': 'SIMULATE', 'live': 'REAL'}
SIDES = {'BUY_OPEN': 'BUY', 'SELL_CLOSE': 'SELL'}
WORKING = {'N/A', 'UNSUBMITTED', 'WAITING_SUBMIT', 'SUBMITTING', 'SUBMITTED', 'TIMEOUT', 'FILLED_PART',
           'CANCELLING_PART', 'CANCELLING_ALL'}
CANCELLED = {'CANCELLED_PART', 'CANCELLED_ALL'}
FAILED = {'SUBMIT_FAILED', 'FAILED', 'DISABLED', 'DELETED'}
MISSING_AFTER_SECONDS = 120

# OpenD / Futu unlock and permission failure fingerprints (EN + ZH).
_UNLOCK_RE = re.compile(
    r'unlock|未解锁|解锁|trade.?pwd|trading.?password|password.*trade|需要.*交易密码|请先解锁',
    re.IGNORECASE,
)
# futu-api transport failures (PacketErr / connect): the request may have reached OpenD.
_TRANSPORT_RE = re.compile(r'time ?out|disconnect|send ?fail|packeterr|failed: invalid$|超时|断开', re.IGNORECASE)


def trade_password_present():
    """True when ``FUTU_TRADE_PASSWORD`` or ``FUTU_TRADE_PASSWORD_MD5`` is non-empty."""
    password, digest = _credentials()
    return bool(password or digest)


def _credentials():
    password = (os.environ.get('FUTU_TRADE_PASSWORD') or '').strip() or None
    digest = (os.environ.get('FUTU_TRADE_PASSWORD_MD5') or '').strip() or None
    return password, digest


def _is_unlock_error(exc):
    return bool(_UNLOCK_RE.search(str(exc)))


def _classify(exc):
    """Hard reject only when OpenD answered; transport failures stay ambiguous."""
    if isinstance(exc, HardSubmitError) or _TRANSPORT_RE.search(str(exc)):
        return exc
    if _is_unlock_error(exc):
        return UnlockRequiredError(str(exc))
    if isinstance(exc, RuntimeError) and str(exc).startswith('OpenD place_order failed:'):
        return HardSubmitError(str(exc))
    return exc


def _rows(result, what):
    ret, data = result
    if ret != 0:
        raise RuntimeError('OpenD %s failed: %s' % (what, data))
    return _records(data)


def order_update(client_order_id, row, now, underlying_mark=None):
    """One OpenD order row as an :class:`OrderUpdate`; ``sequence`` grows only with fills and termination."""
    status = row.get('order_status')
    filled = int(round(_num(row.get('dealt_qty')) or 0))
    if status == 'FILLED_ALL':
        ours = 'FILLED'
    elif status in CANCELLED or (status in FAILED and filled):
        ours = 'CANCELED'
    elif status in FAILED:
        ours = 'REJECTED'
    elif status in WORKING:
        ours = 'PARTIAL' if filled else 'OPEN'
    else:  # FILL_CANCELLED, or a placement whose order row OpenD has not returned yet
        raise RuntimeError('OpenD order %s has status %r; reconcile it before trading on' % (client_order_id, status))
    done = ours in ('FILLED', 'CANCELED', 'REJECTED')
    return OrderUpdate(client_order_id, 2 * filled + done, ours, filled, now, underlying_mark if filled else None,
                       _num(row.get('dealt_avg_price')) if filled else None, now if filled else None)


class OpenDBroker:
    """:class:`custody.ports.Broker` over one OpenD US securities account."""

    def __init__(self, context, market, mode, acc_id):
        if mode not in MODES:
            raise ValueError('broker mode must be paper or live')
        self.ctx, self.market, self.mode = context, market, mode
        self.env, self.acc_id, self.account = MODES[mode], int(acc_id), str(acc_id)
        accounts = _rows(context.get_acc_list(), 'get_acc_list')
        if not any(int(a['acc_id']) == self.acc_id and a['trd_env'] == self.env for a in accounts):
            raise ValueError('acc_id %s is not an OpenD %s account; available: %s' % (
                acc_id, self.env, [(a['acc_id'], a['trd_env'], a.get('acc_type')) for a in accounts]))

    @classmethod
    def connect(cls, market, mode, acc_id, security_firm='FUTUSECURITIES'):
        futu = _futu()
        context = futu.OpenSecTradeContext(filter_trdmarket=futu.TrdMarket.US, host=market.host, port=market.port,
                                           security_firm=security_firm)
        try:
            broker = cls(context, market, mode, acc_id)
            if mode == 'live':
                if not trade_password_present():
                    raise ValueError(
                        'live mode requires FUTU_TRADE_PASSWORD or FUTU_TRADE_PASSWORD_MD5; '
                        'GUI unlock alone expires and leaves place_order stuck until restarted')
                broker.unlock_trade()
            return broker
        except BaseException:
            context.close()
            raise

    def close(self):
        self.ctx.close()

    def unlock_trade(self):
        """Unlock the live trade context using env credentials. No-op for paper / missing env."""
        if self.mode != 'live':
            return False
        password, digest = _credentials()
        if not (password or digest):
            return False
        _rows(self.ctx.unlock_trade(password=password or None, password_md5=digest or None), 'unlock_trade')
        return True

    def submit(self, intent, now):
        cid, code, qty = intent['client_order_id'], intent['contract'], intent['quantity']
        side = SIDES[intent['side']]
        existing = self._find(cid)
        if existing is not None:  # already at OpenD, e.g. restarted on a lost or different database
            return self._update(cid, existing, now)
        if side == 'SELL':
            try:
                held = self._sellable(code)
            except Exception:  # noqa: BLE001 - nothing was sent, so rejecting is authoritative; the exit retries
                return OrderUpdate(cid, 1, 'REJECTED', 0, now)
            if held < qty:
                raise ValueError('close-only: account %s can sell %d of %s, not %d' % (self.account, held, code, qty))
        # Best-effort unlock before every live submit so a GUI unlock expiry cannot stall entries/exits.
        try:
            self.unlock_trade()
        except Exception:  # noqa: BLE001 - place_order still runs; unlock errors classify below
            pass
        for retry in (False, True):
            try:
                if retry:
                    self.unlock_trade()
                return self._place(intent, side, code, qty, cid, now)
            except Exception as exc:  # noqa: BLE001 - may be hard reject, unlock, or ambiguous
                row = self._find(cid) or self._find(cid, refresh=True)
                if row is not None:  # the order exists despite the error reply
                    return self._update(cid, row, now)
                if retry or not (_is_unlock_error(exc) and trade_password_present()):
                    raise _classify(exc) from exc

    def cancel(self, target_client_order_id, cancel_id):
        row = self._find(target_client_order_id)
        if row is None:
            raise RuntimeError('OpenD has no order ' + target_client_order_id)
        if row.get('order_status') in WORKING:  # finished orders need no cancel; lookup reports their final state
            try:
                self.unlock_trade()
            except Exception:  # noqa: BLE001 - cancel may still work if already unlocked
                pass
            for retry in (False, True):
                try:
                    if retry:
                        self.unlock_trade()
                    _rows(self.ctx.modify_order('CANCEL', row['order_id'], 0, 0, trd_env=self.env,
                                                acc_id=self.acc_id), 'modify_order')
                    return
                except Exception as exc:  # noqa: BLE001
                    if retry or not (_is_unlock_error(exc) and trade_password_present()):
                        raise _classify(exc) from exc

    def lookup(self, order, now):
        cid = order['client_order_id']
        row = self._find(cid)
        if (row is None and order['status'] in ('DISPATCHING', 'UNKNOWN')
                and (now - instant(order['created_at'])).total_seconds() >= MISSING_AFTER_SECONDS):
            row = self._find(cid, refresh=True)
            if row is None:
                # ponytail: absent from a refreshed order list 2 minutes after the intent = never placed;
                # an OpenD delay longer than that would need a manual reconcile.
                return OrderUpdate(cid, 1, 'REJECTED', 0, now)
        return None if row is None else self._update(cid, row, now)

    def _place(self, intent, side, code, qty, cid, now):
        rows = _rows(self.ctx.place_order(price=intent['limit_price'], qty=qty, code=code, trd_side=side,
                                          order_type='NORMAL', trd_env=self.env, acc_id=self.acc_id, remark=cid,
                                          time_in_force='DAY', fill_outside_rth=False), 'place_order')
        return self._update(cid, rows[0], now)

    def _find(self, client_order_id, refresh=False):
        rows = _rows(self.ctx.order_list_query(trd_env=self.env, acc_id=self.acc_id, refresh_cache=refresh),
                     'order_list_query')
        found = [r for r in rows if r.get('remark') == client_order_id]
        if len(found) > 1:
            raise RuntimeError('several OpenD orders carry client order id ' + client_order_id)
        return found[0] if found else None

    def _sellable(self, code):
        rows = _rows(self.ctx.position_list_query(code=code, trd_env=self.env, acc_id=self.acc_id, refresh_cache=True),
                     'position_list_query')
        return sum(int(round(_num(r.get('can_sell_qty')) or 0)) for r in rows
                   if r.get('code') == code and r.get('position_side') == 'LONG')

    def _update(self, cid, row, now):
        mark = None
        if row.get('trd_side') == 'BUY' and _num(row.get('dealt_qty')):
            mark = self.market.underlying_mark(parse_option_code(row['code'])[0])
        return order_update(cid, row, now, mark)
