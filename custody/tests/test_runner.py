import ast
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
from helpers import DAY, path_bars, piecewise, session  # noqa: E402

from custody.controller import Controller  # noqa: E402
from custody.dataset import parse_option_code  # noqa: E402
from custody.runner import (Runner, SameDayHistorySource, StrategyFrameSource, _push_notify,  # noqa: E402
                            bar_boundary, build_argument_parser)
from custody.marketdata import normalize_bar_rows  # noqa: E402
from custody.models import ET, Contract, Frame, Quote, Session  # noqa: E402
from custody.opend import OpenDContractResolver, OpenDMarket, OpenDTradingCalendar  # noqa: E402
from custody.registry import Registry  # noqa: E402
from custody.service import CustodyService  # noqa: E402
from custody.strategy import build_strategy  # noqa: E402

T = datetime(2026, 9, 14, 10, 0, tzinfo=ET)
CONTRACT = 'US.QQQ260914P700000'
SID = Registry().default_id


class Catalog:
    def resolve(self, code):
        return Contract(CONTRACT, 'QQQ', DAY, 'PUT', 700.0, 100, 1, 'USD', True)


class Calendar:
    def session(self, day):
        return session(day)


class FakeMarket:
    def __init__(self, quote=None, mark=None):
        self._quote, self._mark = quote, mark

    def quote(self, contract, now=None):
        return self._quote

    def underlying_mark(self, symbol, now=None):
        return self._mark


class FakeFrameSource:
    def __init__(self, frame=None, error=None):
        self.frame_value, self.error, self.calls = frame, error, 0

    def frame(self, job, now=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.frame_value


def quote(when=T):
    return Quote(CONTRACT, 1.00, 1.05, when)


def frame(service, when=T, action='ENTER', reason='trend_breakout'):
    return Frame('QQQ', service.registry.get(SID)['sha256'], when, 700.0, action, reason)


def make_dryrun(path):
    service = CustodyService(path, 'dryrun-test', Catalog(), Calendar(), mode='dryrun')
    job = service.create_job({'strategy_id': SID, 'symbol': 'QQQ', 'direction': 'SHORT',
                              'contract': CONTRACT, 'trade_date': DAY}, T)
    return service, job


def runner_for(service, job, frame_source, simulate=False, mark=700.0, events=None):
    events = events if events is not None else []
    return Runner(service, FakeMarket(quote(), mark), job['id'], frame_source=frame_source, wxpusher_spt='',
                        simulate_fills=simulate, logger=lambda event, **f: events.append({'event': event, **f})), events


class DryRunSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'dryrun.sqlite'

    def tearDown(self):
        self.tmp.cleanup()

    def test_dispatch_is_hard_blocked_in_dryrun(self):
        service, job = make_dryrun(self.path)
        service.on_frame(job['id'], frame(service), T, quote())

        class Spy:
            account, mode = 'dryrun-test', 'dryrun'

            def __init__(self):
                self.calls = []

            def submit(self, intent, now):
                self.calls.append(intent)
                raise AssertionError('must never submit')

            def cancel(self, target, key):
                self.calls.append((target, key))
        spy = Spy()
        with self.assertRaisesRegex(ValueError, 'dryrun'):
            service.dispatch_next(spy, T)
        self.assertEqual(spy.calls, [])

    def test_controller_without_broker_exists_only_for_dryrun(self):
        service, _ = make_dryrun(self.path)
        Controller(service)
        paper = CustodyService(Path(self.tmp.name) / 'paper.sqlite', 'paper', Catalog(), Calendar(), mode='paper')
        with self.assertRaisesRegex(ValueError, 'dryrun'):
            Controller(paper)
        with self.assertRaisesRegex(ValueError, 'dryrun'):
            Runner(paper, FakeMarket(), 'job', simulate_fills=True)

    def test_intent_only_mode_logs_and_keeps_intents_local(self):
        service, job = make_dryrun(self.path)
        calls = []
        service.dispatch_next = lambda *a, **k: calls.append(a)
        runner, events = runner_for(service, job, FakeFrameSource(frame(service)))
        state = runner.tick(T)
        self.assertEqual(state['state'], 'ENTRY')
        self.assertEqual([(o['side'], o['status'], o['cumulative_qty']) for o in state['orders']], [('BUY_OPEN', 'CREATED', 0)])
        intents = [e for e in events if e['event'] == 'order']
        self.assertEqual((len(intents), intents[0]['mode'], intents[0]['status']), (1, 'dryrun', 'CREATED'))
        runner.tick(T + timedelta(seconds=2))
        self.assertEqual(len([e for e in events if e['event'] == 'order']), 1)
        self.assertEqual(calls, [])

    def test_simulated_fills_walk_the_full_lifecycle_without_dispatch(self):
        service, job = make_dryrun(self.path)
        calls = []
        service.dispatch_next = lambda *a, **k: calls.append(a)
        source = FakeFrameSource(frame(service))
        runner, events = runner_for(service, job, source, simulate=True)
        state = runner.tick(T)
        self.assertEqual((state['state'], state['position_qty'], state['entry_underlying']), ('IN', 1, 700.0))
        later = T + timedelta(minutes=3)
        runner.market = FakeMarket(quote(later), 699.0)
        source.frame_value = frame(service, later, 'EXIT', 'invalidation_stop')
        state = runner.tick(later)
        self.assertEqual((state['state'], state['exit_reason']), ('DONE', 'invalidation_stop'))
        fills = [e for e in events if e['event'] == 'simulated_fill']
        self.assertEqual([(f['side'], f['price'], f['submitted']) for f in fills],
                         [('BUY_OPEN', 1.05, False), ('SELL_CLOSE', 1.0, False)])
        self.assertEqual(calls, [])

    def test_frame_and_step_errors_are_non_fatal(self):
        service, job = make_dryrun(self.path)
        runner, events = runner_for(service, job, FakeFrameSource(error=ValueError('no history')))
        self.assertEqual(runner.tick(T)['orders'], [])
        self.assertIn('frame_error', {e['event'] for e in events})
        runner.frame_source = FakeFrameSource(frame(service, T + timedelta(minutes=5)))
        self.assertIsNone(runner.tick(T))
        self.assertIn('step_error', {e['event'] for e in events})
        runner.frame_source = FakeFrameSource(frame(service))
        self.assertEqual(runner.tick(T)['state'], 'ENTRY')

    def test_run_survives_repeated_step_errors(self):
        service, job = make_dryrun(self.path)
        runner, events = runner_for(service, job, FakeFrameSource(frame(service)))
        runner.interval = 0.0

        def boom(*a, **k):
            raise ValueError('future or stale frame')
        runner.controller.step = boom
        self.assertEqual(runner.run(ticks=2), 2)
        self.assertEqual([e['event'] for e in events].count('step_error'), 2)

    def test_run_ends_when_the_job_is_done(self):
        service, job = make_dryrun(self.path)
        runner, events = runner_for(service, job, FakeFrameSource())
        service.stop_job(job['id'], T)
        self.assertEqual(runner.run(ticks=5), 1)
        self.assertEqual(events[-1]['event'], 'done')

    def test_only_the_broker_references_the_trade_api(self):
        forbidden = {'OpenSecTradeContext', 'place_order', 'unlock_trade', 'modify_order'}
        package = Path(__file__).resolve().parents[1]
        for path in package.rglob('*.py'):
            if 'tests' in path.relative_to(package).parts or path.name == 'broker.py':
                continue
            name = path.name
            tree = ast.parse(path.read_text())
            used = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    used.add(node.attr)
                elif isinstance(node, ast.Name):
                    used.add(node.id)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    used.update(a.name.split('.')[-1] for a in node.names)
            self.assertEqual(used & forbidden, set(), name)


class FrameSourceTests(unittest.TestCase):
    class History:
        def __init__(self, bars):
            self.bars, self.calls = bars, 0

        def collect(self, boundary):
            self.calls += 1
            return session(), [b for b in self.bars if b.close_time <= boundary]

    def job(self, entry_at=None, entry_underlying=None, direction='LONG'):
        item = Registry().get(SID)
        return {'strategy': item, 'request': {'symbol': 'SPY', 'direction': direction}, 'contract': {'strike': 100.0},
                'entry_at': entry_at, 'entry_underlying': entry_underlying}

    def test_bar_boundary_floors_to_completed_regular_minutes(self):
        self.assertIsNone(bar_boundary(T.replace(hour=9, minute=30, second=30)))
        self.assertEqual(bar_boundary(T.replace(hour=9, minute=31, second=45)), T.replace(hour=9, minute=31))
        self.assertIsNone(bar_boundary(T.replace(hour=16, minute=1)))

    def test_replay_matches_the_engine_and_injects_the_entry_fill(self):
        bars = path_bars(piecewise([(1, 100.0), (15, 100.0), (390, 110.0)]))
        s = session()
        engine = build_strategy(Registry().get(SID), 'LONG', s, 100.0)
        entry_minute = next(i for i, b in enumerate(bars, 1) if engine.on_bar(b).action == 'ENTER')
        source = StrategyFrameSource(self.History(bars))
        at = bars[entry_minute - 1].close_time
        f = source.frame(self.job(), at + timedelta(seconds=2))
        self.assertEqual((f.action, f.reason, f.bar_close), ('ENTER', 'trend_breakout', at))
        fill_at = at + timedelta(seconds=4)
        later = bars[entry_minute + 30].close_time
        f2 = StrategyFrameSource(self.History(bars)).frame(self.job(fill_at.isoformat(), bars[entry_minute].close),
                                                           later + timedelta(seconds=1))
        self.assertEqual(f2.action, 'HOLD')
        self.assertIsNotNone(f2.diagnostics['progress_atr'])

    def test_caches_per_boundary_and_expires(self):
        bars = path_bars(piecewise([(1, 100.0), (390, 100.0)]))
        history = self.History(bars)
        source = StrategyFrameSource(history)
        self.assertIsNotNone(source.frame(self.job(), T))
        source.frame(self.job(), T + timedelta(seconds=3))
        self.assertEqual(history.calls, 1)
        self.assertIsNone(source.frame(self.job(), T + timedelta(seconds=30)))
        self.assertEqual(history.calls, 1)

    def test_same_day_history_requires_every_minute(self):
        s = session()
        rows = [{'time_key': (s.opens + timedelta(minutes=i)).strftime('%Y-%m-%d %H:%M:%S'), 'open': 1, 'high': 2,
                 'low': .5, 'close': 1.5, 'volume': 10} for i in range(1, 31)]

        class Market:
            def __init__(self, rows):
                self.rows = rows

            def current_bars(self, code, count, ktype, boundary=None):
                return []

            def history_bars(self, code, ktype, start, end, boundary=None):
                return normalize_bar_rows(self.rows, code, boundary=boundary)
        source = SameDayHistorySource(Market(rows), Calendar(), 'SPY')
        got_session, bars = source.collect(s.opens + timedelta(minutes=30))
        self.assertEqual((got_session.day, len(bars)), (DAY, 30))
        gappy = SameDayHistorySource(Market(rows[:10] + rows[11:]), Calendar(), 'US.SPY')
        with self.assertRaisesRegex(ValueError, 'not ready'):
            gappy.collect(s.opens + timedelta(minutes=30))


class WxPusherNotifyTests(unittest.TestCase):
    SPT = 'SPT_TEST_ONLY'

    def test_spt_builds_expected_url(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"code":1000}'
        with mock.patch('custody.runner.urllib.request.urlopen', return_value=response) as urlopen:
            _push_notify('dryrun BUY_OPEN X', 'limit=1.05', wxpusher_spt=self.SPT)
        url = urlopen.call_args.args[0]
        self.assertTrue(url.startswith('https://wxpusher.zjiecode.com/api/send/message/%s/' % self.SPT))
        self.assertEqual(urllib.parse.unquote(url.split(self.SPT + '/', 1)[1]), 'dryrun BUY_OPEN X\nlimit=1.05')

    def test_missing_spt_or_network_error_never_raises(self):
        with mock.patch('custody.runner.urllib.request.urlopen') as urlopen:
            _push_notify('t', 'b', wxpusher_spt=None)
            _push_notify('t', 'b', wxpusher_spt='')
        urlopen.assert_not_called()
        with mock.patch('custody.runner.urllib.request.urlopen', side_effect=urllib.error.URLError('down')):
            _push_notify('t', 'b', wxpusher_spt=self.SPT)

    def test_runner_reads_env_spt(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, job = make_dryrun(Path(tmp) / 'push.sqlite')
            with mock.patch.dict(os.environ, {'CUSTODY_WXPUSHER_SPT': self.SPT}):
                runner = Runner(service, FakeMarket(quote(), 700.0), job['id'],
                                      frame_source=FakeFrameSource(frame(service)), logger=lambda *a, **k: None)
            with mock.patch('custody.runner.urllib.request.urlopen', side_effect=urllib.error.URLError('down')) as urlopen:
                self.assertIsNotNone(runner.tick(T))
            urlopen.assert_called_once()


class Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def to_dict(self, orient='records'):
        return list(self._rows)


class FakeQuoteContext:
    def __init__(self, snapshot=None, trading_days=None):
        self.snapshot_rows, self.trading = snapshot or [], trading_days or []

    def get_market_snapshot(self, codes):
        wanted = {str(c).upper() for c in codes}
        return 0, Rows([r for r in self.snapshot_rows if str(r.get('code', '')).upper() in wanted])

    def request_trading_days(self, market=None, start=None, end=None):
        return 0, list(self.trading)

    def close(self):
        pass


class OpenDAdapterTests(unittest.TestCase):
    def test_contract_resolver_uses_snapshot_metadata(self):
        row = {'code': CONTRACT, 'option_type': 'PUT', 'option_strike_price': 700.0, 'strike_time': DAY,
               'option_contract_multiplier': 100.0, 'option_valid': True, 'sec_status': 'NORMAL'}
        contract = OpenDContractResolver(OpenDMarket(quote_context=FakeQuoteContext(snapshot=[row]))).resolve(CONTRACT)
        self.assertEqual((contract.underlying, contract.expiry, contract.right, contract.strike, contract.tradable),
                         ('QQQ', DAY, 'PUT', 700.0, True))
        self.assertEqual(parse_option_code(CONTRACT), ('QQQ', DAY, 'PUT', 700.0))

    def test_market_quote_and_mark_parsing(self):
        snapshot = [{'code': CONTRACT, 'bid_price': 4.35, 'ask_price': 4.50, 'update_time': '2026-09-14 10:00:00'},
                    {'code': 'US.QQQ', 'last_price': 701.2}]
        market = OpenDMarket(quote_context=FakeQuoteContext(snapshot=snapshot))
        q = market.quote(CONTRACT, T)
        self.assertEqual((q.bid, q.ask, q.as_of), (4.35, 4.50, T))
        self.assertEqual(market.underlying_mark('US.QQQ'), 701.2)
        crossed = OpenDMarket(quote_context=FakeQuoteContext(snapshot=[dict(snapshot[0], bid_price=0)]))
        self.assertIsNone(crossed.quote(CONTRACT, T))

    def test_calendar_regular_and_early_close(self):
        days = [{'time': DAY, 'trade_date_type': 'WHOLE'}, {'time': '2026-11-27', 'trade_date_type': 'MORNING'}]
        calendar = OpenDTradingCalendar(OpenDMarket(quote_context=FakeQuoteContext(trading_days=days)))
        self.assertEqual(calendar.session(DAY).closes.strftime('%H:%M'), '16:00')
        self.assertEqual(calendar.session('2026-11-27').closes.strftime('%H:%M'), '13:00')
        with self.assertRaisesRegex(ValueError, 'trading day'):
            calendar.session('2026-09-19')

    def test_context_guard_rejects_trade_like_class(self):
        class OpenSecTradeContext:  # noqa: N801
            pass
        with self.assertRaisesRegex(RuntimeError, 'read-only'):
            OpenDMarket(quote_context=OpenSecTradeContext()).context


class CliTests(unittest.TestCase):
    ARGS = ['--symbol', 'US.QQQ', '--direction', 'SHORT', '--contract', CONTRACT]

    def test_dryrun_and_run_arguments(self):
        args = build_argument_parser('dryrun').parse_args(self.ARGS)
        self.assertEqual((args.strategy, args.host, args.port, args.max_qty, args.intent_only, args.db),
                         (None, None, None, 1, False, None))
        run = build_argument_parser('run').parse_args(self.ARGS + ['--mode', 'paper', '--acc-id', '281756'])
        self.assertEqual((run.mode, run.acc_id, run.security_firm), ('paper', 281756, 'FUTUSECURITIES'))
        with contextlib.redirect_stderr(io.StringIO()):
            for missing in (['--mode', 'paper'], ['--acc-id', '1'], ['--mode', 'dryrun', '--acc-id', '1']):
                with self.assertRaises(SystemExit):
                    build_argument_parser('run').parse_args(self.ARGS + missing)

    def test_status_and_stop_work_on_a_runtime_database(self):
        from custody.__main__ import main
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'dryrun.sqlite'
            _, job = make_dryrun(path)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(['status', '--db', str(path)]), 0)
                self.assertEqual(main(['stop', '--db', str(path), '--job', job['id']]), 0)
            listed, stopped = [json.loads(line) for line in out.getvalue().splitlines()]
            self.assertEqual((listed['job_id'], listed['state']), (job['id'], 'IDLE'))
            self.assertEqual((stopped['state'], stopped['exit_reason']), ('DONE', 'operator_stop'))
            with self.assertRaises(SystemExit):
                main(['status', '--db', str(path), '--job', 'missing'])
            with self.assertRaisesRegex(ValueError, 'no custody database'):
                main(['status', '--db', str(Path(tmp) / 'none.sqlite')])

    def test_module_cli_help(self):
        from custody.__main__ import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            main(['run', '--help'])
        self.assertEqual(caught.exception.code, 0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([]), 0)
        self.assertTrue(all(name in out.getvalue() for name in ('evaluate', 'dryrun', 'run', 'status', 'stop')))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['baseline']), 2)


if __name__ == '__main__':
    unittest.main()
