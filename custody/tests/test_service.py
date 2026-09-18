import hashlib
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from custody.controller import Controller
from custody.models import ET, Contract, Frame, JobRequest, OrderUpdate, Quote, Session
from custody.registry import Registry
from custody.service import CustodyService

DAY = '2026-09-14'
T = datetime(2026, 9, 14, 10, 0, tzinfo=ET)
SID = Registry().default_id
CALL, PUT = 'US.SPY260914C600000', 'US.SPY260914P600000'


class Catalog:
    def resolve(self, code):
        expiry = '2026-09-18' if code == 'US.SPY260918C600000' else DAY
        return Contract(code, 'SPY', expiry, 'PUT' if 'P6' in code else 'CALL', 600)


class Calendar:
    def session(self, day):
        d = datetime.fromisoformat(day)
        return Session(day, d.replace(hour=9, minute=30, tzinfo=ET), d.replace(hour=16, tzinfo=ET))


class Adapter:
    account, mode = 'test', 'paper'

    def __init__(self):
        self.calls, self.cancels, self.events = [], [], {}

    def submit(self, order, now):
        self.calls.append(order)
        return OrderUpdate(order['client_order_id'], 0, 'OPEN', 0, now)

    def cancel(self, target, key):
        self.cancels.append((target, key))

    def lookup(self, order, now):
        return self.events.get(order['client_order_id'])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'jobs.sqlite'
        self.service = CustodyService(self.path, 'test', Catalog(), Calendar())
        self.request = {'strategy_id': SID, 'symbol': 'SPY', 'direction': 'LONG', 'contract': CALL,
                        'max_qty': 2, 'trade_date': DAY}

    def tearDown(self):
        self.tmp.cleanup()

    # helpers ------------------------------------------------------------
    def job(self, now=T):
        return self.service.create_job(self.request, now)

    def quote(self, t=T, bid=1.0, ask=1.05, contract=CALL):
        return Quote(contract, bid, ask, t)

    def frame(self, t=T, action='ENTER', reason='trend_breakout'):
        return Frame('SPY', self.service.registry.get(SID)['sha256'], t, 600.0, action, reason)

    def entry(self):
        j = self.job()
        self.service.on_frame(j['id'], self.frame(), T, self.quote())
        adapter = Adapter()
        self.service.dispatch_next(adapter, T)
        return j, adapter, adapter.calls[0]['client_order_id']

    def fill(self, key, qty=2, status='FILLED', seq=1, when=T):
        first = when if status in ('FILLED', 'PARTIAL') or qty else None
        return self.service.apply_update(OrderUpdate(key, seq, status, qty, when, 600.0, 1.05, first), when)

    def orders(self, j, side=None):
        return [o for o in self.service.get_job(j['id'])['orders'] if side is None or o['side'] == side]

    # request / identity ---------------------------------------------------
    def test_minimal_input_and_strict_types(self):
        payload = {k: v for k, v in self.request.items() if k not in ('max_qty', 'trade_date')}
        request = JobRequest.parse(payload, T)
        self.assertEqual((request.max_qty, request.trade_date, request.symbol), (1, DAY, 'SPY'))
        for bad in (dict(payload, dry_run=False), dict(payload, max_qty=True), dict(payload, max_qty=1.5),
                    dict(payload, direction='SELL')):
            with self.assertRaises(ValueError):
                JobRequest.parse(bad, T)

    def test_only_same_day_contracts_matching_the_direction(self):
        with self.assertRaisesRegex(ValueError, '0DTE'):
            self.service.create_job(dict(self.request, contract='US.SPY260918C600000'), T)
        with self.assertRaisesRegex(ValueError, 'right/direction'):
            self.service.create_job(dict(self.request, contract=PUT), T)
        with self.assertRaises(ValueError):
            self.service.create_job(dict(self.request, strategy_id='orb_rvol_rsi_1m_v1'), T)

    def test_one_job_per_contract_per_day_idempotent_and_concurrent(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: self.job()['id'], range(8)))
        self.assertEqual(len(set(ids)), 1)
        with self.assertRaisesRegex(ValueError, 'one_job_per_contract_day'):
            self.service.create_job(dict(self.request, max_qty=1), T)

    def test_same_underlying_can_trade_long_and_short_contracts_the_same_day(self):
        call_job = self.job()
        put_job = self.service.create_job(dict(self.request, direction='SHORT', contract=PUT), T)
        other_strike = self.service.create_job(dict(self.request, contract='US.SPY260914C605000'), T)
        self.assertEqual(len({call_job['id'], put_job['id'], other_strike['id']}), 3)
        self.service.on_frame(call_job['id'], self.frame(), T, self.quote())
        self.service.on_frame(put_job['id'], self.frame(), T, self.quote(contract=PUT))
        self.assertEqual([o['contract'] for o in self.orders(call_job)], [CALL])
        self.assertEqual([o['contract'] for o in self.orders(put_job)], [PUT])

    def test_mode_binding_old_schema_and_live_requires_accepted_strategy(self):
        with self.assertRaisesRegex(ValueError, 'another mode'):
            CustodyService(self.path, 'test', Catalog(), Calendar(), mode='live')
        old = Path(self.tmp.name) / 'old.sqlite'
        with sqlite3.connect(old) as db:
            db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY)')
        with self.assertRaisesRegex(ValueError, 'older custody runtime'):
            CustodyService(old, 'test', Catalog(), Calendar())
        live = CustodyService(Path(self.tmp.name) / 'live.sqlite', 'live', Catalog(), Calendar(), mode='live')
        with self.assertRaisesRegex(ValueError, 'accepted'):
            live.create_job(self.request, T)

    def test_entry_window_closes_at_flatten(self):
        late = T.replace(hour=15, minute=45)
        with self.assertRaisesRegex(ValueError, 'entry window closed'):
            self.service.create_job(self.request, late)
        with self.assertRaisesRegex(ValueError, 'today'):
            self.service.create_job(self.request, T - timedelta(days=1))

    def test_registry_is_immutable_and_has_one_default(self):
        registry = Registry()
        self.assertEqual(registry.default_id, SID)
        usable = [s['strategy_id'] for s in registry.list() if s['status'] != 'retired']
        self.assertEqual(usable, [SID])
        item = registry.get(SID)
        item['config']['params']['trail_atr'] = 99
        self.assertNotEqual(registry.get(SID)['config']['params']['trail_atr'], 99)
        source = Path(registry.root)
        for name, mutate, message in (
                ('changed', lambda text: text.replace('"trail_atr": 5.0', '"trail_atr": 6.0'), 'immutable'),
                ('status_in_file', lambda text: text.replace('"engine"', '"status": "accepted",\n  "engine"'), 'status belongs')):
            root = Path(self.tmp.name) / name
            root.mkdir()
            for path in source.iterdir():
                (root / path.name).write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
            target = root / (SID + '.json')
            target.write_text(mutate(target.read_text(encoding='utf-8')), encoding='utf-8')
            if name == 'status_in_file':  # re-pin the hash so the status rule itself is exercised
                index = json.loads((root / 'index.json').read_text(encoding='utf-8'))
                index['strategies'][SID]['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
                (root / 'index.json').write_text(json.dumps(index), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, message):
                Registry(root)

    def test_retired_strategy_cannot_start_a_job(self):
        item = self.service.registry.get(SID)
        item['status'] = 'retired'
        with patch.object(self.service.registry, 'get', return_value=item):
            with self.assertRaisesRegex(ValueError, 'retired'):
                self.service.create_job(self.request, T)

    # lifecycle ------------------------------------------------------------
    def test_enter_fill_exit_fill_is_one_round_trip(self):
        j, adapter, key = self.entry()
        self.assertEqual(self.service.get_job(j['id'])['state'], 'ENTRY')
        state = self.fill(key)
        self.assertEqual((state['state'], state['position_qty'], state['entry_underlying']), ('IN', 2, 600.0))
        later = T + timedelta(minutes=5)
        self.service.on_frame(j['id'], self.frame(later, 'EXIT', 'trailing_stop'), later, self.quote(later))
        self.service.dispatch_next(adapter, later)
        sell = self.orders(j, 'SELL_CLOSE')[0]
        self.assertEqual((sell['quantity'], sell['reduce_only'], sell['reason'], sell['limit_price']), (2, True, 'trailing_stop', 1.0))
        state = self.fill(sell['client_order_id'], when=later)
        self.assertEqual((state['state'], state['position_qty'], state['exit_reason']), ('DONE', 0, 'trailing_stop'))

    def test_heartbeat_never_forces_entry_and_empty_job_finishes(self):
        j = self.job()
        for hour in (13, 14, 15):
            now = T.replace(hour=hour)
            state = self.service.heartbeat(j['id'], now, self.quote(now, 0.5, 1.0))
            self.assertEqual(state['orders'], [])
        end = datetime.fromisoformat(j['flatten_at'])
        state = self.service.heartbeat(j['id'], end, self.quote(end))
        self.assertEqual((state['state'], state['attention'], state['orders']), ('DONE', 'NO_ENTRY_SIGNAL', []))
        state = self.service.on_frame(j['id'], self.frame(end), end, self.quote(end))
        self.assertEqual(state['orders'], [])

    def test_signal_blocked_by_quote_is_not_reported_as_no_signal(self):
        j = self.job()
        self.service.on_frame(j['id'], self.frame(), T, None)
        end = datetime.fromisoformat(j['flatten_at'])
        state = self.service.heartbeat(j['id'], end, self.quote(end))
        self.assertEqual((state['state'], state['attention'], state['orders']), ('DONE', 'ENTRY_NOT_FILLED', []))

    def test_wide_or_stale_quote_blocks_a_strategy_entry(self):
        j = self.job()
        self.service.on_frame(j['id'], self.frame(), T, self.quote(bid=0.5, ask=1.0))
        self.service.on_frame(j['id'], self.frame(T + timedelta(minutes=1)), T + timedelta(minutes=1), self.quote(T - timedelta(seconds=10)))
        state = self.service.get_job(j['id'])
        self.assertEqual((state['orders'], state['attention']), ([], 'ENTRY_WAITING_VALID_QUOTE'))

    def test_frame_identity_and_timing_checks(self):
        j = self.job()
        with self.assertRaises(ValueError):
            self.service.on_frame(j['id'], self.frame(T + timedelta(minutes=1)), T, self.quote())
        with self.assertRaises(ValueError):
            self.service.on_frame(j['id'], self.frame(T - timedelta(minutes=1)), T, self.quote())
        with self.assertRaises(ValueError):
            self.service.on_frame(j['id'], self.frame(T + timedelta(seconds=30)), T + timedelta(seconds=30), self.quote())
        bad = Frame('SPY', 'not-the-hash', T, 600.0, 'ENTER')
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.service.on_frame(j['id'], bad, T, self.quote())

    def test_unfilled_entry_is_retried_until_filled(self):
        j = self.job()
        self.service.on_frame(j['id'], self.frame(), T, self.quote())
        later = T + timedelta(seconds=31)
        state = self.service.heartbeat(j['id'], later, self.quote(later))
        self.assertEqual((state['state'], state['attention']), ('WATCH', 'ENTRY_RETRY_PENDING'))
        nxt = T + timedelta(minutes=1)
        self.service.on_frame(j['id'], self.frame(nxt), nxt, self.quote(nxt))
        self.assertEqual([o['client_order_id'].rsplit(':', 1)[1] for o in self.orders(j, 'BUY_OPEN')], ['BUY_1', 'BUY_2'])

    def test_rejected_entry_returns_to_watch(self):
        j, adapter, key = self.entry()
        state = self.service.apply_update(OrderUpdate(key, 1, 'REJECTED', 0, T), T)
        self.assertEqual((state['state'], state['attention']), ('WATCH', 'ENTRY_RETRY_PENDING'))

    def test_unknown_submission_is_not_retried(self):
        j = self.job()
        self.service.on_frame(j['id'], self.frame(), T, self.quote())

        class Timeout(Adapter):
            def submit(self, order, now):
                self.calls.append(order)
                raise TimeoutError()
        adapter = Timeout()
        self.assertIn('unknown', self.service.dispatch_next(adapter, T))
        self.assertIsNone(self.service.dispatch_next(adapter, T))
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(self.orders(j)[0]['status'], 'UNKNOWN')

    def test_restart_keeps_job_and_outbox(self):
        j, adapter, key = self.entry()
        self.service = CustodyService(self.path, 'test', Catalog(), Calendar())
        self.assertEqual(self.job()['id'], j['id'])
        self.assertIsNone(self.service.dispatch_next(adapter, T))
        self.assertEqual(len(adapter.calls), 1)

    def test_partial_entry_then_exit_cancels_remainder_and_sells_only_owned(self):
        j, adapter, key = self.entry()
        self.fill(key, 1, 'PARTIAL')
        later = T + timedelta(minutes=6)
        self.service.on_frame(j['id'], self.frame(later, 'EXIT', 'invalidation_stop'), later, self.quote(later))
        self.service.dispatch_next(adapter, later)
        state = self.service.get_job(j['id'])
        self.assertEqual((state['state'], state['position_qty'], state['attention']), ('EXIT', 1, 'WAITING_ENTRY_CANCEL_CONFIRMATION'))
        self.assertEqual(self.orders(j, 'SELL_CLOSE'), [])
        self.assertEqual(adapter.cancels[0][0], key)
        self.fill(key, 1, 'CANCELED', 2, later)
        self.service.heartbeat(j['id'], later, self.quote(later))
        self.assertEqual(self.orders(j, 'SELL_CLOSE')[0]['quantity'], 1)

    def test_partial_exit_resells_only_the_remainder(self):
        j, adapter, key = self.entry()
        self.fill(key)
        later = T + timedelta(minutes=6)
        self.service.on_frame(j['id'], self.frame(later, 'EXIT', 'no_progress'), later, self.quote(later))
        sell = self.orders(j, 'SELL_CLOSE')[0]
        self.service.apply_update(OrderUpdate(sell['client_order_id'], 0, 'CANCELED', 1, later, None, 1.0), later)
        self.service.heartbeat(j['id'], later, self.quote(later))
        last = self.orders(j, 'SELL_CLOSE')[-1]
        self.assertEqual((last['quantity'], last['reduce_only']), (1, True))
        state = self.service.apply_update(OrderUpdate(last['client_order_id'], 0, 'FILLED', 1, later, None, 1.0), later)
        self.assertEqual(state['state'], 'DONE')

    def test_resting_exit_is_repriced_after_timeout(self):
        j, adapter, key = self.entry()
        self.fill(key)
        later = T + timedelta(minutes=6)
        self.service.on_frame(j['id'], self.frame(later, 'EXIT', 'invalidation_stop'), later, self.quote(later))
        self.service.dispatch_next(adapter, later)
        stale = later + timedelta(seconds=31)
        state = self.service.heartbeat(j['id'], stale, self.quote(stale))
        self.assertEqual(state['attention'], 'EXIT_REPRICE_CANCEL_PENDING')
        self.assertEqual(len([o for o in state['orders'] if o['kind'] == 'CANCEL']), 1)

    def test_duplicate_and_inconsistent_fill(self):
        j, adapter, key = self.entry()
        self.fill(key)
        self.fill(key, when=T + timedelta(seconds=5))  # the same broker state polled again later
        self.assertEqual(self.service.get_job(j['id'])['position_qty'], 2)
        with self.assertRaises(ValueError):
            self.fill(key, 1, 'PARTIAL')
        with self.assertRaises(ValueError):
            self.fill(key, 3, 'FILLED', 2)

    def test_flatten_needs_no_bars_and_waits_for_a_quote(self):
        j, adapter, key = self.entry()
        self.fill(key)
        end = datetime.fromisoformat(j['flatten_at'])
        state = self.service.heartbeat(j['id'], end)
        self.assertEqual((state['state'], state['attention'], state['exit_reason']), ('EXIT', 'EXIT_WAITING_VALID_QUOTE', 'scheduled_flatten'))
        self.service.heartbeat(j['id'], end, self.quote(end))
        self.assertEqual(self.orders(j)[-1]['side'], 'SELL_CLOSE')

    def test_unsent_entry_is_canceled_at_flatten(self):
        j = self.job()
        end = datetime.fromisoformat(j['flatten_at'])
        bar = end - timedelta(minutes=1)
        self.service.on_frame(j['id'], self.frame(bar), bar + timedelta(seconds=5), self.quote(bar + timedelta(seconds=5)))
        state = self.service.heartbeat(j['id'], end, self.quote(end))
        self.assertEqual((state['state'], state['attention']), ('DONE', 'ENTRY_NOT_FILLED'))
        self.assertTrue(all(o['status'] == 'CANCELED' for o in state['orders']))

    def test_stop_before_entry_creates_no_order(self):
        j = self.job()
        stopped = self.service.stop_job(j['id'], T)
        self.assertEqual((stopped['state'], stopped['orders'], stopped['attention']), ('DONE', [], None))

    def test_broker_lookup_failure_does_not_skip_flatten_cancel(self):
        j, adapter, key = self.entry()

        def broken(_order, _now):
            raise TimeoutError()
        adapter.lookup = broken
        end = datetime.fromisoformat(j['flatten_at'])
        state = Controller(self.service, adapter).step(j['id'], end, self.quote(end))
        self.assertEqual((state['state'], state['attention']), ('EXIT', 'RECONCILE_ORDER_STATUS'))
        self.assertEqual(len(adapter.cancels), 1)

    def test_controller_requires_matching_broker(self):
        with self.assertRaisesRegex(ValueError, 'broker required'):
            Controller(self.service)

        class Other(Adapter):
            account = 'other'
        with self.assertRaisesRegex(ValueError, 'binding'):
            Controller(self.service, Other())


if __name__ == '__main__':
    unittest.main()
