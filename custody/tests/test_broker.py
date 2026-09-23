import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta
from pathlib import Path

from custody.broker import OpenDBroker, order_update, trade_password_present
from custody.models import HardSubmitError, UnlockRequiredError
from custody.controller import Controller
from custody.models import ET, Contract, Frame, Quote, Session
from custody.registry import Registry
from custody.service import CustodyService

DAY = '2026-09-14'
T = datetime(2026, 9, 14, 10, 0, tzinfo=ET)
CALL = 'US.SPY260914C600000'
SIM = 281756


class FakeTradeContext:
    """OpenSecTradeContext stand-in: orders fill only when a test says so."""

    def __init__(self):
        self.orders, self.positions, self.placed, self.cancelled, self.fail = [], {}, [], [], set()
        self.unlock_calls = 0

    def get_acc_list(self):
        return 0, [{'acc_id': SIM, 'trd_env': 'SIMULATE', 'acc_type': 'MARGIN'},
                   {'acc_id': 1001, 'trd_env': 'REAL', 'acc_type': 'MARGIN'}]

    def place_order(self, **order):
        self.placed.append(order)
        if 'place_order' in self.fail:  # never reached OpenD
            return -1, 'timeout'
        self.orders.append({'code': order['code'], 'trd_side': order['trd_side'], 'order_status': 'SUBMITTED',
                            'order_id': str(len(self.placed)), 'dealt_qty': 0.0, 'dealt_avg_price': 0.0,
                            'remark': order['remark']})
        return (-1, 'timeout') if 'response_lost' in self.fail else (0, [dict(self.orders[-1])])

    def order_list_query(self, trd_env, acc_id, refresh_cache=False):
        return (-1, 'down') if 'order_list_query' in self.fail else (0, [dict(o) for o in self.orders])

    def position_list_query(self, code, trd_env, acc_id, refresh_cache=False):
        if 'position_list_query' in self.fail:
            return -1, 'down'
        held = self.positions.get(code, 0)
        return 0, [{'code': code, 'position_side': 'LONG', 'can_sell_qty': float(held)}] if held else []

    def modify_order(self, op, order_id, qty, price, trd_env, acc_id):
        self.cancelled.append((op, order_id))
        return 0, []

    def unlock_trade(self, password=None, password_md5=None):
        self.unlock_calls += 1
        if 'unlock_trade' in self.fail:
            return -1, 'please unlock trade first'
        return 0, 'unlocked'

    def close(self):
        pass

    def fill(self, index, qty, price, status='FILLED_ALL'):
        self.orders[index].update(order_status=status, dealt_qty=float(qty), dealt_avg_price=price)


class Market:
    def underlying_mark(self, symbol, now=None):
        return 600.5


def intent(side='BUY_OPEN', key='job:BUY_1', status='DISPATCHING'):
    return {'client_order_id': key, 'side': side, 'contract': CALL, 'quantity': 1, 'limit_price': 1.05,
            'created_at': T.isoformat(), 'status': status}


class OrderStatusTests(unittest.TestCase):
    def test_opend_statuses_map_to_growing_sequences(self):
        def update(status, dealt):
            u = order_update('k', {'order_status': status, 'dealt_qty': dealt, 'dealt_avg_price': 1.2}, T, 600.0)
            return u.status, u.sequence, u.cumulative_qty
        self.assertEqual([update(*row) for row in (('SUBMITTED', 0), ('CANCELLING_ALL', 0), ('FILLED_PART', 1),
                                                   ('CANCELLED_PART', 1), ('FILLED_ALL', 2), ('CANCELLED_ALL', 0),
                                                   ('FAILED', 0))],
                         [('OPEN', 0, 0), ('OPEN', 0, 0), ('PARTIAL', 2, 1), ('CANCELED', 3, 1), ('FILLED', 5, 2),
                          ('CANCELED', 1, 0), ('REJECTED', 1, 0)])
        for odd in ('FILL_CANCELLED', float('nan')):
            with self.assertRaises(RuntimeError):
                update(odd, 0)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeTradeContext()
        self.broker = OpenDBroker(self.ctx, Market(), 'paper', SIM)

    def test_account_must_exist_in_the_requested_environment(self):
        with self.assertRaisesRegex(ValueError, 'SIMULATE'):
            OpenDBroker(FakeTradeContext(), Market(), 'paper', 1001)
        with self.assertRaisesRegex(ValueError, 'paper or live'):
            OpenDBroker(FakeTradeContext(), Market(), 'dryrun', SIM)
        self.assertEqual(OpenDBroker(FakeTradeContext(), Market(), 'live', 1001).env, 'REAL')

    def test_buy_is_a_day_limit_order_found_again_by_its_client_id(self):
        update = self.broker.submit(intent(), T)
        self.assertEqual((update.status, update.sequence), ('OPEN', 0))
        placed = self.ctx.placed[0]
        self.assertEqual({k: placed[k] for k in ('code', 'trd_side', 'order_type', 'qty', 'price', 'remark',
                                                 'time_in_force', 'fill_outside_rth', 'trd_env', 'acc_id')},
                         {'code': CALL, 'trd_side': 'BUY', 'order_type': 'NORMAL', 'qty': 1, 'price': 1.05,
                          'remark': 'job:BUY_1', 'time_in_force': 'DAY', 'fill_outside_rth': False,
                          'trd_env': 'SIMULATE', 'acc_id': SIM})
        self.ctx.fill(0, 1, 1.04)
        later = T + timedelta(seconds=5)
        filled = self.broker.lookup(intent(status='OPEN'), later)
        self.assertEqual((filled.status, filled.sequence, filled.cumulative_qty, filled.average_option_price,
                          filled.underlying_mark, filled.first_fill_at), ('FILLED', 3, 1, 1.04, 600.5, later))

    def test_an_id_already_at_opend_is_never_placed_again(self):
        first = self.broker.submit(intent(), T)
        again = OpenDBroker(self.ctx, Market(), 'paper', SIM).submit(intent(), T + timedelta(minutes=1))
        self.assertEqual((len(self.ctx.placed), again.status, again.sequence), (1, first.status, first.sequence))

    def test_sell_only_closes_what_the_account_holds(self):
        with self.assertRaisesRegex(ValueError, 'close-only'):
            self.broker.submit(intent('SELL_CLOSE', 'job:SELL_1'), T)
        self.ctx.fail.add('position_list_query')
        self.assertEqual(self.broker.submit(intent('SELL_CLOSE', 'job:SELL_1'), T).status, 'REJECTED')
        self.assertEqual(self.ctx.placed, [])
        self.ctx.fail.clear()
        self.ctx.positions[CALL] = 1
        self.assertEqual(self.broker.submit(intent('SELL_CLOSE', 'job:SELL_1'), T).status, 'OPEN')
        self.assertEqual(self.ctx.placed[0]['trd_side'], 'SELL')

    def test_failed_placement_is_hard_reject_when_absent_from_order_list(self):
        self.ctx.fail.add('place_order')
        with self.assertRaises(HardSubmitError):
            self.broker.submit(intent(), T)
        unknown = intent(status='UNKNOWN')
        self.assertIsNone(self.broker.lookup(unknown, T + timedelta(seconds=30)))
        self.assertIsNone(self.broker.lookup(dict(unknown, status='OPEN'), T + timedelta(minutes=3)))
        rejected = self.broker.lookup(unknown, T + timedelta(minutes=3))
        self.assertEqual((rejected.status, rejected.sequence), ('REJECTED', 1))

    def test_lost_place_reply_recovers_from_order_list(self):
        self.ctx.fail = {'response_lost'}  # the order exists, only the reply was lost
        update = self.broker.submit(intent(key='job:BUY_2'), T)
        self.assertEqual(update.status, 'OPEN')
        self.assertEqual(len(self.ctx.placed), 1)

    def test_unlock_error_retries_once_and_succeeds(self):
        live = OpenDBroker(self.ctx, Market(), 'live', 1001)
        self.ctx.fail.add('place_order')
        # First failure message looks like unlock; password present → unlock + retry once.
        original_place = self.ctx.place_order
        calls = {'n': 0}

        def flaky(**order):
            calls['n'] += 1
            self.ctx.placed.append(order)
            if calls['n'] == 1:
                return -1, 'please unlock trade first'
            return original_place(**order)

        self.ctx.place_order = flaky
        with unittest.mock.patch.dict('os.environ', {'FUTU_TRADE_PASSWORD': 'x'}, clear=False):
            # clear place_order fail set so retry can succeed via flaky's second call
            self.ctx.fail.discard('place_order')
            update = live.submit(intent(key='job:BUY_3'), T)
        self.assertEqual(update.status, 'OPEN')
        self.assertGreaterEqual(self.ctx.unlock_calls, 1)
        self.assertEqual(calls['n'], 2)

    def test_unlock_failure_without_recovery_is_unlock_required(self):
        live = OpenDBroker(self.ctx, Market(), 'live', 1001)
        self.ctx.fail.add('place_order')
        original_place = self.ctx.place_order

        def always_locked(**order):
            self.ctx.placed.append(order)
            return -1, 'please unlock trade first'

        self.ctx.place_order = always_locked
        with unittest.mock.patch.dict('os.environ', {'FUTU_TRADE_PASSWORD': 'x'}, clear=False):
            with self.assertRaises(UnlockRequiredError):
                live.submit(intent(key='job:BUY_4'), T)
        self.assertGreaterEqual(self.ctx.unlock_calls, 1)

    def test_trade_password_present_ignores_empty(self):
        with unittest.mock.patch.dict('os.environ', {'FUTU_TRADE_PASSWORD': '', 'FUTU_TRADE_PASSWORD_MD5': ''}, clear=False):
            self.assertFalse(trade_password_present())
        with unittest.mock.patch.dict('os.environ', {'FUTU_TRADE_PASSWORD': 'secret'}, clear=False):
            self.assertTrue(trade_password_present())

    def test_cancel_targets_working_orders_only(self):
        self.broker.submit(intent(), T)
        self.broker.cancel('job:BUY_1', 'job:CANCEL_BUY_1')
        self.assertEqual(self.ctx.cancelled, [('CANCEL', '1')])
        self.ctx.fill(0, 1, 1.04)
        self.broker.cancel('job:BUY_1', 'job:CANCEL_BUY_1')
        self.assertEqual(len(self.ctx.cancelled), 1)
        with self.assertRaises(RuntimeError):
            self.broker.cancel('job:BUY_9', 'job:CANCEL_BUY_9')


class PaperRoundTripTests(unittest.TestCase):
    def test_signal_to_buy_fill_to_exit_fill_through_the_service(self):
        class Catalog:
            def resolve(self, code):
                return Contract(CALL, 'SPY', DAY, 'CALL', 600.0)

        class Calendar:
            def session(self, day):
                return Session(day, T.replace(hour=9, minute=30), T.replace(hour=16))

        ctx = FakeTradeContext()
        broker = OpenDBroker(ctx, Market(), 'paper', SIM)
        registry = Registry()
        with tempfile.TemporaryDirectory() as tmp:
            service = CustodyService(Path(tmp) / 'paper.sqlite', str(SIM), Catalog(), Calendar(), mode='paper')
            job = service.create_job({'strategy_id': registry.default_id, 'symbol': 'SPY', 'direction': 'LONG',
                                      'contract': CALL, 'trade_date': DAY}, T)
            sha, controller = job['strategy']['sha256'], Controller(service, broker)

            def step(at, action=None):
                frame = Frame('SPY', sha, at.replace(second=0), 600.0, action, action.lower()) if action else None
                return controller.step(job['id'], at, Quote(CALL, 1.0, 1.05, at), frame)

            state = step(T, 'ENTER')
            self.assertEqual((state['state'], state['orders'][0]['status']), ('ENTRY', 'OPEN'))
            state = step(T + timedelta(seconds=3))  # the unchanged working order is polled again
            self.assertEqual((state['state'], state['attention']), ('ENTRY', None))
            ctx.fill(0, 1, 1.05)
            state = step(T + timedelta(seconds=5))
            self.assertEqual((state['state'], state['position_qty'], state['entry_underlying']), ('IN', 1, 600.5))
            ctx.positions[CALL] = 1
            state = step(T + timedelta(minutes=5), 'EXIT')
            self.assertEqual(([p['trd_side'] for p in ctx.placed], state['state']), (['BUY', 'SELL'], 'EXIT'))
            ctx.fill(1, 1, 1.30)
            state = step(T + timedelta(minutes=5, seconds=5))
            self.assertEqual((state['state'], state['position_qty'], state['exit_reason']), ('DONE', 0, 'exit'))


if __name__ == '__main__':
    unittest.main()
