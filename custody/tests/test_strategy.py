import json
import sys
import unittest
from dataclasses import replace
from datetime import time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from helpers import BASE_PARAMS, path_bars, piecewise, session  # noqa: E402

from custody.engines.zero_dte_timing import ZeroDteTiming, validate_params  # noqa: E402
from custody.indicators import SessionIndicators, bachelier  # noqa: E402
from custody.registry import Registry  # noqa: E402
from custody.strategy import Decision, build_strategy, flatten_minute, session_minute  # noqa: E402

PARAMS = BASE_PARAMS


def run(closes, direction='LONG', params=PARAMS, fill=True, strike=100.0):
    """Feed bars; report the entry fill at the next bar close (like the evaluator)."""
    s = session()
    engine = ZeroDteTiming(params, direction, s, strike)
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

    def test_flatten_follows_the_real_session_close(self):
        self.assertEqual(flatten_minute(PARAMS, session()), 375)
        self.assertEqual(flatten_minute(PARAMS, session(close=time(13))), 195)
        with self.assertRaises(ValueError):
            flatten_minute(PARAMS, session(close=time(9, 40)))

    def test_session_minute_rejects_partial_or_outside_bars(self):
        s = session()
        self.assertEqual(session_minute(s, s.opens + timedelta(minutes=1)), 1)
        for bad in (s.opens, s.opens + timedelta(seconds=90), s.closes + timedelta(minutes=1)):
            with self.assertRaises(ValueError):
                session_minute(s, bad)
        with self.assertRaises(ValueError):
            Decision('BUY')


class EntryScenarioTests(unittest.TestCase):
    def test_current_strategy_waits_without_confirmation_for_both_directions(self):
        params = Registry().get(Registry().default_id)['config']['params']
        falling = piecewise([(1, 100), (390, 90)])
        for closes, direction in ((falling, 'LONG'), ([200 - c for c in falling], 'SHORT'), ([100] * 390, 'LONG')):
            decisions = run(closes, direction, params, strike=100)
            self.assertTrue(all(action == 'WAIT' for _, action, _ in decisions))
        for removed in ('must_enter_before_close_minutes', 'friction_force_z'):
            with self.assertRaisesRegex(ValueError, 'unknown'):
                validate_params({**params, removed: 1})


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

    def test_trend_against_all_day_stays_flat(self):
        closes = piecewise([(1, 100.0), (390, 96.0)])
        self.assertEqual(first(run(closes), 'ENTER'), (None, None))

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
        engine = ZeroDteTiming(PARAMS, 'LONG', s, 100.0)
        bars = path_bars(piecewise([(1, 100.0), (390, 100.0)]))
        with self.assertRaises(ValueError):
            engine.on_entry_filled(bars[0].close_time, 100.0)  # nothing seen yet
        engine.on_bar(bars[0])
        with self.assertRaises(ValueError):
            engine.on_entry_filled(bars[0].close_time, 100.0)
        trend = path_bars(piecewise([(1, 100.0), (390, 110.0)]))
        engine = ZeroDteTiming(PARAMS, 'LONG', s, 100.0)
        for bar in trend:
            if engine.on_bar(bar).action == 'ENTER':
                engine.on_entry_filled(bar.close_time, bar.close)
                break
        self.assertEqual(engine.phase, 'IN')
        with self.assertRaises(ValueError):
            engine.on_entry_filled(bar.close_time, bar.close)


class ContractAwareRuleTests(unittest.TestCase):
    def test_engine_requires_the_contract_strike(self):
        for bad in (None, 0, True):
            with self.assertRaisesRegex(ValueError, 'strike', msg=str(bad)):
                ZeroDteTiming(PARAMS, 'LONG', session(), bad)

    def test_out_of_the_money_entries_get_a_tighter_stop(self):
        def risk(extra):
            engine = ZeroDteTiming({**PARAMS, **extra}, 'LONG', session(), 120.0)
            for bar in path_bars(piecewise([(1, 100), (390, 110)])):
                if engine.on_bar(bar).action == 'ENTER':
                    return engine.risk_distance / engine.entry_atr, engine._otm_z(engine.ind.minute)
        plain, z = risk({})
        shrunk, _ = risk({'otm_stop_shrink': 0.25})
        self.assertGreaterEqual(z, 1.0)
        self.assertAlmostEqual(shrunk, plain * max(0.5, 1 - 0.25 * z))

    def test_protection_only_counts_once_the_option_is_at_the_money(self):
        rising = [100.0] * 15 + piecewise([(1, 100.0), (375, 110.0)], 375)
        charm = {**PARAMS, 'charm_exit_before_close_minutes': 90}
        self.assertEqual(first(run(rising, params=charm, strike=115.0), 'EXIT')[1], 'scheduled_flatten')
        gated = {**charm, 'protection_needs_moneyness': 1}
        self.assertEqual(first(run(rising, params=gated, strike=115.0), 'EXIT'), (300, 'charm_exit'))
        self.assertEqual(first(run(rising, params=gated, strike=101.0), 'EXIT')[1], 'scheduled_flatten')

class DeterminismTests(unittest.TestCase):
    def test_prefix_replay_gives_identical_decisions(self):
        closes = piecewise([(1, 100.0), (5, 100.0), (60, 98.5), (150, 101.5), (300, 99.0), (390, 100.5)])
        full = run(closes)
        for cut in (30, 90, 151, 240):
            self.assertEqual(run(closes[:cut]), full[:cut])

    def test_registered_files_have_the_documented_shape(self):
        registry = Registry()
        for item in registry.list():
            doc = json.loads((registry.root / (item['strategy_id'] + '.json')).read_text(encoding='utf-8'))
            self.assertEqual(set(doc), {'schema_version', 'strategy_id', 'engine', 'description', 'developed_on', 'params'})
            self.assertEqual(doc['developed_on']['release'], 'custody-0dte-v5')
        self.assertIn(registry.default_id, [i['strategy_id'] for i in registry.list() if i['status'] != 'retired'])


class OptionalRuleTests(unittest.TestCase):
    def test_optional_parameters_validate(self):
        engine = ZeroDteTiming(PARAMS, 'LONG', session(), 100.0)
        self.assertEqual(engine.p['persist_minutes'], 0)
        for bad in ({'persist_minutes': -1}, {'relax_after_minutes': 5}):
            with self.assertRaises(ValueError, msg=str(bad)):
                validate_params({**PARAMS, **bad})

    def test_entry_filter_parameters_validate(self):
        name = 'min_breakout_volume_ratio'
        self.assertIsNone(validate_params(PARAMS)[name])
        for value in (None, 0.1, 5.0):
            self.assertEqual(validate_params({**PARAMS, name: value})[name], value)
        for value in (True, False, float('nan'), float('inf'), -float('inf'), '1.0', 0.09, 5.01):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_params({**PARAMS, name: value})

    def test_disabled_entry_filters_preserve_decisions(self):
        closes = piecewise([(1, 100), (15, 100), (60, 103), (90, 101), (390, 105)])
        disabled = {**PARAMS, 'min_breakout_volume_ratio': None, 'squeeze_lookback': None,
                    'profit_lock_at': None, 'profit_lock_keep': None}
        for direction, path in (('LONG', closes), ('SHORT', [200 - c for c in closes])):
            self.assertEqual(run(path, direction), run(path, direction, disabled))

    def test_bachelier_and_session_sigma(self):
        self.assertAlmostEqual(bachelier(0.0, 1.0), 1 / (2 * 3.141592653589793) ** 0.5)
        self.assertEqual(bachelier(2.0, 0.0), 2.0)
        self.assertEqual(bachelier(-2.0, 0.0), 0.0)
        self.assertAlmostEqual(bachelier(1.0, 0.5) - bachelier(-1.0, 0.5), 1.0)   # put-call parity
        ind = SessionIndicators(opening_minutes=1)
        for i, bar in enumerate(path_bars([100.0, 101.0, 100.0, 101.0]), start=1):
            ind.update(bar, i)
        self.assertAlmostEqual(ind.sigma, 1.0)

    def test_take_profit_sells_once_the_estimated_premium_doubles(self):
        closes = piecewise([(1, 100.0), (15, 100.0), (390, 110.0)])
        entry, _ = first(run(closes), 'ENTER')
        minute, reason = first(run(closes, params={**PARAMS, 'take_profit_premium': 1.0}), 'EXIT')
        self.assertEqual(reason, 'take_profit')
        self.assertLess(minute, 375)
        self.assertGreater(minute, entry)
        for bad in (0.0, -1.0, True):
            with self.assertRaises(ValueError, msg=str(bad)):
                validate_params({**PARAMS, 'take_profit_premium': bad})

    def test_profit_lock_keeps_part_of_the_peak_and_take_profit_still_sells_first(self):
        run_up = piecewise([(1, 100.0), (15, 100.0), (390, 110.0)])
        up_down = piecewise([(1, 100.0), (15, 100.0), (40, 101.0), (200, 101.0), (390, 100.0)])
        # Only the premium rules can sell: breakeven, trail and the time stop are out of reach.
        loose = {**PARAMS, 'breakeven_at_atr': 50.0, 'trail_activate_atr': 50.0, 'trail_atr': 50.0, 'fail_minutes': 390}
        locked = {**loose, 'profit_lock_at': 1.0, 'profit_lock_keep': 0.5}
        for direction, mirror in (('LONG', lambda c: c), ('SHORT', lambda c: 200 - c)):
            with self.subTest(direction=direction):
                up, down = [mirror(c) for c in run_up], [mirror(c) for c in up_down]
                self.assertEqual(first(run(up, direction, locked), 'EXIT'), (375, 'scheduled_flatten'))
                minute, reason = first(run(down, direction, locked), 'EXIT')
                self.assertEqual(reason, 'profit_lock')
                self.assertGreater(minute, 200)
                capped = first(run(up, direction, {**loose, 'take_profit_premium': 1.0}), 'EXIT')
                self.assertEqual(capped[1], 'take_profit')
                self.assertEqual(first(run(up, direction, {**locked, 'take_profit_premium': 1.0}), 'EXIT'), capped)
        for bad in ({'profit_lock_at': 0.5}, {'profit_lock_keep': 0.5},
                    {'profit_lock_at': 0.5, 'profit_lock_keep': 1.0}, {'profit_lock_at': 0.0, 'profit_lock_keep': 0.5},
                    {'profit_lock_at': True, 'profit_lock_keep': 0.5}):
            with self.assertRaises(ValueError, msg=str(bad)):
                validate_params({**PARAMS, **bad})

    def test_squeeze_lets_through_only_breakouts_out_of_a_recent_compression(self):
        base = piecewise([(1, 100.0), (40, 100.0), (60, 101.0), (390, 106.0)])     # 40 flat bars, then a breakout
        steady = piecewise([(1, 100.0), (390, 110.0)])                            # trends from the open, never compressed
        squeeze = {**PARAMS, 'squeeze_lookback': 10}
        for direction, mirror in (('LONG', lambda c: c), ('SHORT', lambda c: 200 - c)):
            with self.subTest(direction=direction):
                out_of_base = [mirror(c) for c in base]
                self.assertEqual(first(run(out_of_base, direction, squeeze), 'ENTER'), first(run(out_of_base, direction), 'ENTER'))
                trending = [mirror(c) for c in steady]
                self.assertEqual(first(run(trending, direction), 'ENTER')[1], 'trend_breakout')
                self.assertEqual(first(run(trending, direction, squeeze), 'ENTER'), (None, None))
        for bad in (0, 1.5, True):
            with self.assertRaises(ValueError, msg=str(bad)):
                validate_params({**PARAMS, 'squeeze_lookback': bad})

    def test_no_out_of_the_money_entry_inside_the_charm_window(self):
        late = [100.0] * 300 + piecewise([(1, 100.0), (90, 104.0)], 90)
        charm = {**PARAMS, 'charm_exit_before_close_minutes': 90}
        self.assertEqual(first(run(late, params=PARAMS, strike=110.0), 'ENTER')[1], 'trend_breakout')
        self.assertEqual(first(run(late, params=charm, strike=110.0), 'ENTER'), (None, None))
        self.assertEqual(first(run(late, params=charm, strike=100.0), 'ENTER')[1], 'trend_breakout')

    def test_persistence_rejects_a_fresh_breakout_until_the_vwap_side_has_held(self):
        closes = piecewise([(1, 100.0), (15, 100.0), (390, 110.0)])
        quick, _ = first(run(closes), 'ENTER')
        held, reason = first(run(closes, params={**PARAMS, 'persist_minutes': 20}), 'ENTER')
        self.assertEqual(reason, 'trend_breakout')
        self.assertGreaterEqual(held, quick + 15)

    def test_retired_premium_breakeven_parameter_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_params({**PARAMS, 'premium_breakeven_at': 0.1})

    def test_no_entry_while_the_estimated_premium_is_small_against_the_atr(self):
        closes = piecewise([(1, 100.0), (15, 100.0), (390, 110.0)])
        quick = first(run(closes), 'ENTER')
        self.assertEqual(first(run(closes, params={**PARAMS, 'min_premium_atr': 1.0}), 'ENTER'), quick)
        # at the money the estimate is too small at first; deep in the money later it is not
        later, reason = first(run(closes, params={**PARAMS, 'min_premium_atr': 100.0}), 'ENTER')
        self.assertEqual(reason, 'trend_breakout')
        self.assertGreater(later, quick[0])
        self.assertEqual(first(run(closes, params={**PARAMS, 'min_premium_atr': 1.0}, strike=150.0), 'ENTER'),
                         (None, None))
        for bad in (0.0, True):
            with self.assertRaises(ValueError, msg=str(bad)):
                validate_params({**PARAMS, 'min_premium_atr': bad})

    def test_no_entry_while_the_close_has_run_away_from_vwap(self):
        burst = piecewise([(1, 100.0), (15, 100.0), (30, 103.0), (60, 102.0), (390, 108.0)])
        steady = piecewise([(1, 100.0), (15, 100.0), (390, 110.0)])
        capped = {**PARAMS, 'max_vwap_atr': 2.0}
        self.assertEqual(first(run(burst), 'ENTER'), (16, 'trend_breakout'))
        self.assertEqual(first(run(burst, params=capped), 'ENTER'), (None, None))
        self.assertEqual(first(run([200 - c for c in burst], 'SHORT', params=capped), 'ENTER'), (None, None))
        self.assertEqual(first(run(steady, params=capped), 'ENTER'), first(run(steady), 'ENTER'))
        for bad in (0.25, 0.0, True):   # must exceed vwap_buffer_atr (0.25)
            with self.assertRaises(ValueError, msg=str(bad)):
                validate_params({**PARAMS, 'max_vwap_atr': bad})


class BreakoutFilterTests(unittest.TestCase):
    def breakout_action(self, direction, extra, prior_volume=100.0, **last):
        bars = path_bars([100.0] * 15 + [103.0], volume=10000.0)
        # Only the five immediately preceding bars belong to the volume reference.
        bars[10:15] = [replace(bar, volume=prior_volume) for bar in bars[10:15]]
        bars[-1] = replace(bars[-1], **{'open': 100.0, 'low': 100.0, 'high': 104.0,
                                      'close': 103.0, 'volume': 200.0, **last})
        if direction == 'SHORT':
            bars = [replace(bar, open=200 - bar.open, high=200 - bar.low,
                            low=200 - bar.high, close=200 - bar.close) for bar in bars]
        engine = ZeroDteTiming({**PARAMS, **extra}, direction, session(), 100.0)
        actions = [engine.on_bar(bar).action for bar in bars]
        self.assertEqual(actions[:-1], ['WAIT'] * 15)
        return actions[-1]

    def test_volume_uses_only_prior_window_and_accepts_equality(self):
        for direction in ('LONG', 'SHORT'):
            for prior, current, expected in ((100, 200, 'ENTER'), (100, 199, 'WAIT'),
                                             (100, 0, 'WAIT'), (0, 200, 'WAIT')):
                with self.subTest(direction=direction, prior=prior, current=current):
                    self.assertEqual(self.breakout_action(direction, {}, prior, volume=current), 'ENTER')
                    self.assertEqual(self.breakout_action(direction, {'min_breakout_volume_ratio': 2.0},
                                                          prior, volume=current), expected)


if __name__ == '__main__':
    unittest.main()
