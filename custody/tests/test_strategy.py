import json
import sys
import unittest
from datetime import time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from helpers import path_bars, piecewise, session  # noqa: E402

from custody.engines.zero_dte_timing import ZeroDteTiming, validate_params  # noqa: E402
from custody.indicators import SessionIndicators  # noqa: E402
from custody.registry import Registry  # noqa: E402
from custody.strategy import Decision, build_strategy, deadlines, session_minute  # noqa: E402

PARAMS = Registry().get('zero_dte_timing_v1')['config']['params']


def run(closes, direction='LONG', params=PARAMS, fill=True):
    """Feed bars; report the entry fill at the next bar close (like the evaluator)."""
    s = session()
    engine = ZeroDteTiming(params, direction, s)
    decisions, pending = [], None
    for bar in path_bars(closes):
        if fill and pending is not None and engine.phase == 'ENTERING':
            engine.on_entry_filled(bar.close_time, bar.close)
            pending = None
        d = engine.on_bar(bar)
        decisions.append((session_minute(s, bar.close_time), d.action, d.reason))
        if d.action == 'ENTER' and pending is None and engine.phase == 'ENTERING':
            pending = decisions[-1][0]
    return decisions


def first(decisions, action):
    return next(((m, r) for m, a, r in decisions if a == action), (None, None))


class IndicatorTests(unittest.TestCase):
    def test_vwap_ema_atr_opening_range_and_prior_bars(self):
        ind = SessionIndicators(ema_fast=2, ema_slow=3, atr_period=2, opening_minutes=2)
        bars = path_bars([100.0, 101.0, 102.0], wick=0.5)
        for i, bar in enumerate(bars, start=1):
            ind.update(bar, i)
        self.assertEqual((ind.or_high, ind.or_low), (101.5, 99.5))
        self.assertEqual((ind.high, ind.low), (102.5, 99.5))
        typical = [(b.high + b.low + b.close) / 3 for b in bars]
        self.assertAlmostEqual(ind.vwap, sum(typical) / 3)
        self.assertAlmostEqual(ind.atr, (2.0 + 2.0) / 2)  # last two true ranges
        self.assertGreater(ind.ema_fast, ind.ema_slow)
        self.assertEqual([b.close for b in ind.prior_bars(5)], [100.0, 101.0])
        with self.assertRaises(ValueError):
            ind.update(bars[0], 3)


class ParameterTests(unittest.TestCase):
    def test_registered_params_validate_and_bad_params_fail(self):
        validate_params(PARAMS)
        for change in ({'unknown': 1}, {'ema_fast': 30}, {'stop_min_atr': 5.0}, {'opening_minutes': 0},
                       {'fail_minutes': 1.5}, {'relax_after_minutes': 10}, {'trail_atr': True}):
            with self.assertRaises(ValueError, msg=str(change)):
                validate_params({**PARAMS, **change})
        missing = dict(PARAMS)
        missing.pop('trail_atr')
        with self.assertRaises(ValueError):
            validate_params(missing)

    def test_deadlines_follow_the_real_session_close(self):
        self.assertEqual(deadlines(PARAMS, session()), (390 - PARAMS['must_enter_before_close_minutes'],
                                                        390 - PARAMS['flatten_before_close_minutes']))
        early = session(close=time(13))
        self.assertEqual(deadlines(PARAMS, early)[1], 210 - 15)
        with self.assertRaises(ValueError):
            build_strategy(Registry().get('zero_dte_timing_v1'), 'LONG', session(close=time(11)))

    def test_session_minute_rejects_partial_or_outside_bars(self):
        s = session()
        self.assertEqual(session_minute(s, s.opens + timedelta(minutes=1)), 1)
        for bad in (s.opens, s.opens + timedelta(seconds=90), s.closes + timedelta(minutes=1)):
            with self.assertRaises(ValueError):
                session_minute(s, bad)
        with self.assertRaises(ValueError):
            Decision('BUY')


class EntryScenarioTests(unittest.TestCase):
    def test_trend_day_enters_right_after_the_opening_range(self):
        closes = piecewise([(1, 100.0), (15, 100.0), (200, 109.0), (390, 112.0)])
        minute, reason = first(run(closes), 'ENTER')
        self.assertEqual(reason, 'trend_breakout')
        self.assertTrue(PARAMS['opening_minutes'] <= minute <= PARAMS['opening_minutes'] + 10, minute)

    def test_no_entry_inside_the_opening_range(self):
        closes = piecewise([(1, 100.0), (390, 120.0)])
        minute, _ = first(run(closes), 'ENTER')
        self.assertGreaterEqual(minute, PARAMS['opening_minutes'])

    def test_early_drop_then_reversal_waits_for_the_reclaim(self):
        closes = piecewise([(1, 100.0), (5, 100.0), (60, 98.5), (150, 101.5), (390, 102.0)])
        minute, reason = first(run(closes), 'ENTER')
        self.assertEqual(reason, 'reversal_reclaim')
        self.assertGreater(minute, 60)

    def test_trend_against_all_day_enters_only_at_the_must_trade_deadline(self):
        closes = piecewise([(1, 100.0), (390, 96.0)])
        minute, reason = first(run(closes), 'ENTER')
        self.assertEqual((minute, reason), (390 - PARAMS['must_enter_before_close_minutes'], 'must_trade_deadline'))

    def test_short_direction_is_the_exact_mirror_of_long(self):
        closes = piecewise([(1, 100.0), (5, 100.0), (60, 98.5), (150, 101.5), (300, 99.0), (390, 100.5)])
        mirrored = [200.0 - c for c in closes]
        self.assertEqual(run(closes, 'LONG'), run(mirrored, 'SHORT'))


class ExitScenarioTests(unittest.TestCase):
    def rise_then(self, tail):
        return piecewise([(1, 100.0), (15, 100.0), (25, 100.5)] + tail)

    def test_adverse_move_hits_the_invalidation_stop(self):
        closes = [100.0] * 15 + [100.05, 100.1] + piecewise([(1, 100.1), (5, 99.8), (400, 99.0)], 373)
        decisions = run(closes)
        entry, _ = first(decisions, 'ENTER')
        exit_minute, reason = first(decisions, 'EXIT')
        self.assertEqual(reason, 'invalidation_stop')
        self.assertTrue(entry < exit_minute <= entry + 5, (entry, exit_minute))

    def test_winner_runs_then_trails_out_after_a_real_pullback(self):
        decisions = run(self.rise_then([(90, 104.0), (110, 101.0), (390, 100.0)]))
        exit_minute, reason = first(decisions, 'EXIT')
        self.assertEqual(reason, 'trailing_stop')
        self.assertGreater(exit_minute, 90)

    def test_trade_without_progress_is_cut_for_theta(self):
        closes = [100.0] * 15 + [100.05, 100.1] + [100.08] * 373
        decisions = run(closes)
        entry, _ = first(decisions, 'ENTER')
        exit_minute, reason = first(decisions, 'EXIT')
        self.assertEqual(reason, 'no_progress')
        self.assertEqual(exit_minute, entry + 1 + PARAMS['fail_minutes'])

    def test_all_day_winner_exits_at_scheduled_flatten(self):
        decisions = run(piecewise([(1, 100.0), (15, 100.0), (390, 120.0)]))
        self.assertEqual(first(decisions, 'EXIT'), (390 - PARAMS['flatten_before_close_minutes'], 'scheduled_flatten'))
        self.assertTrue(all(a == 'EXIT' for m, a, _ in decisions if m >= 375))

    def test_entry_stays_pending_until_the_fill_is_reported(self):
        decisions = run(piecewise([(1, 100.0), (15, 100.0), (390, 110.0)]), fill=False)
        entry, _ = first(decisions, 'ENTER')
        self.assertTrue(all(a == 'ENTER' for m, a, _ in decisions if entry <= m < 375))

    def test_fill_contract(self):
        s = session()
        engine = ZeroDteTiming(PARAMS, 'LONG', s)
        bars = path_bars(piecewise([(1, 100.0), (390, 100.0)]))
        with self.assertRaises(ValueError):
            engine.on_entry_filled(bars[0].close_time, 100.0)  # nothing seen yet
        engine.on_bar(bars[0])
        engine.on_entry_filled(bars[0].close_time, 100.0)       # platform-forced entry is accepted
        self.assertEqual((engine.phase, engine.entry_reason), ('IN', 'platform_entry'))
        with self.assertRaises(ValueError):
            engine.on_entry_filled(bars[1].close_time, 100.0)


class DeterminismTests(unittest.TestCase):
    def test_prefix_replay_gives_identical_decisions(self):
        closes = piecewise([(1, 100.0), (5, 100.0), (60, 98.5), (150, 101.5), (300, 99.0), (390, 100.5)])
        full = run(closes)
        for cut in (30, 90, 151, 240):
            self.assertEqual(run(closes[:cut]), full[:cut])

    def test_registered_file_is_json_with_the_documented_shape(self):
        doc = json.loads((Path(__file__).resolve().parents[1] / 'strategies' / 'zero_dte_timing_v1.json').read_text())
        self.assertEqual(set(doc), {'schema_version', 'strategy_id', 'engine', 'status', 'description', 'developed_on', 'params'})
        self.assertEqual(doc['developed_on']['release'], 'custody-train-0dte')


if __name__ == '__main__':
    unittest.main()
