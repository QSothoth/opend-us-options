import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from helpers import DAY, option_bars, path_bars, piecewise, session, write_dataset  # noqa: E402

from custody import evaluate as ev  # noqa: E402
from custody.dataset import Case, CaseData, Dataset  # noqa: E402
from custody.marketdata import Bar  # noqa: E402
from custody.registry import Registry  # noqa: E402
from custody.strategy import Decision, session_minute  # noqa: E402

ITEM = Registry().get(Registry().default_id)
PARAMS = ITEM['config']['params']
CALL, PUT = 'US.SPY260914C100000', 'US.SPY260914P100000'


def case_data(closes, direction='LONG', option=None, prev_close=None):
    s = session()
    contract = CALL if direction == 'LONG' else PUT
    opt = option if option is not None else option_bars(closes, 100, 'CALL' if direction == 'LONG' else 'PUT', contract)
    return CaseData(Case('US.SPY', contract, DAY, direction, 100.0, prev_close), s, tuple(path_bars(closes)), tuple(opt))


class Scripted:
    """Engine stub: ENTER/EXIT at given minutes; records fills."""

    def __init__(self, enter=None, exit_=None):
        self.enter, self.exit, self.filled = enter, exit_, None
        self.s = session()

    def on_bar(self, bar):
        m = session_minute(self.s, bar.close_time)
        if self.filled is None:
            return Decision('ENTER', 'scripted') if self.enter is not None and m >= self.enter else Decision('WAIT')
        return Decision('EXIT', 'scripted') if self.exit is not None and m >= self.exit else Decision('HOLD')

    def on_entry_filled(self, at, mark):
        self.filled = (at, mark)


class FillModelTests(unittest.TestCase):
    def test_slippage_is_against_us_and_capped_by_the_bar(self):
        bar = Bar('X', session().opens + timedelta(minutes=1), 1.0, 1.4, 0.6, 1.0, 5)
        fill = ev.FillModel(slippage_fraction=0.25)
        self.assertAlmostEqual(fill.buy(bar), 1.2)
        self.assertAlmostEqual(fill.sell(bar), 0.8)
        self.assertEqual(ev.FillModel(slippage_fraction=1.0).buy(bar), 1.4)
        self.assertEqual(ev.FillModel(slippage_fraction=1.0).sell(bar), 0.6)
        for bad in (dict(delay_minutes=0), dict(slippage_fraction=1.5), dict(fee_per_contract=-1)):
            with self.assertRaises(ValueError):
                ev.FillModel(**bad)


class SimulationTests(unittest.TestCase):
    closes = piecewise([(1, 100.0), (390, 101.0)])

    def test_fills_happen_on_the_first_traded_bar_after_the_delay(self):
        option = option_bars(self.closes, 100, 'CALL', CALL, every=3)  # trades at minutes 3, 6, 9, ...
        data = case_data(self.closes, option=option)
        engine = Scripted(enter=10, exit_=20)
        trade = ev.simulate(data, engine, PARAMS, ev.FillModel(slippage_fraction=0.0, fee_per_contract=0.0))
        self.assertEqual((trade['entry']['minute'], trade['entry']['fill_minute']), (10, 12))
        self.assertEqual((trade['exit']['minute'], trade['exit']['fill_minute']), (20, 21))
        self.assertEqual(engine.filled[0], session().opens + timedelta(minutes=12))
        entry_bar = next(b for b in option if b.close_time == engine.filled[0])
        self.assertAlmostEqual(trade['net_return'], trade['exit']['price'] / entry_bar.close - 1)
        self.assertEqual(trade['hold_minutes'], 9)

    def test_fees_are_charged_on_both_sides(self):
        data = case_data(self.closes)
        trade = ev.simulate(data, Scripted(enter=10, exit_=20), PARAMS, ev.FillModel(slippage_fraction=0.0, fee_per_contract=0.65))
        gross = (trade['exit']['price'] - trade['entry']['price']) * 100
        self.assertAlmostEqual(trade['net_pnl'], gross - 1.30, places=3)

    def test_platform_forces_entry_and_flatten_when_the_engine_does_not(self):
        trade = ev.simulate(case_data(self.closes), Scripted(), PARAMS)
        must_enter, flatten = 390 - PARAMS['must_enter_before_close_minutes'], 390 - PARAMS['flatten_before_close_minutes']
        self.assertEqual((trade['entry']['reason'], trade['entry']['minute']), ('platform_must_enter', must_enter))
        self.assertEqual((trade['exit']['reason'], trade['exit']['minute']), ('platform_flatten', flatten))
        self.assertIsNone(trade['failure'])

    def test_unfillable_entry_is_a_failure_not_a_zero(self):
        option = [b for b in option_bars(self.closes, 100, 'CALL', CALL) if session_minute(session(), b.close_time) < 30]
        trade = ev.simulate(case_data(self.closes, option=option), Scripted(enter=40, exit_=50), PARAMS)
        self.assertEqual(trade['failure'], 'ENTRY_NOT_FILLED')
        self.assertIsNone(trade['net_return'])

    def test_unsellable_position_is_settled_at_expiry_value(self):
        option = [b for b in option_bars(self.closes, 100, 'CALL', CALL) if session_minute(session(), b.close_time) <= 100]
        trade = ev.simulate(case_data(self.closes, option=option), Scripted(enter=50, exit_=200), PARAMS,
                            ev.FillModel(slippage_fraction=0.0))
        self.assertIsNone(trade['failure'])
        self.assertTrue(trade['exit']['settled_at_expiry'])
        intrinsic = max(0.0, self.closes[-1] - 100)
        self.assertAlmostEqual(trade['exit']['price'], intrinsic)
        paid = trade['entry']['price'] * 100
        self.assertAlmostEqual(trade['net_return'], ((intrinsic - trade['entry']['price']) * 100 - 0.65) / paid, places=4)


class LabelTests(unittest.TestCase):
    def label(self, points, direction='LONG', prev_close=None):
        closes = piecewise(points)
        return ev.label_case(case_data(closes, direction, prev_close=prev_close), 375)

    def test_day_shapes_relative_to_direction(self):
        self.assertEqual(self.label([(1, 100), (60, 101), (390, 104)])['scenario'], 'trend_with')
        self.assertEqual(self.label([(1, 100), (60, 101), (390, 104)], 'SHORT')['scenario'], 'trend_against')
        self.assertEqual(self.label([(1, 100), (60, 97), (390, 102)])['scenario'], 'reversal_with')
        self.assertEqual(self.label([(1, 100), (60, 97), (390, 102)], 'SHORT')['scenario'], 'reversal_against')
        self.assertEqual(self.label([(1, 100), (30, 100.2), (60, 100), (200, 99.9), (390, 100.05)])['scenario'], 'chop')

    def test_market_shape_needs_the_previous_close(self):
        self.assertEqual(self.label([(1, 100), (390, 104)])['market_shape'], 'unknown_prev_close')
        self.assertEqual(self.label([(1, 100), (390, 104)], prev_close=101.0)['market_shape'], '低开高走')
        self.assertEqual(self.label([(1, 100), (390, 96)], prev_close=99.0)['market_shape'], '高开低走')


class WeightingAndMetricsTests(unittest.TestCase):
    def test_mirror_weights_neutralise_the_direction_mix(self):
        labels = ['trend_with'] * 1 + ['trend_against'] * 9 + ['chop'] * 2 + ['reversal_with'] * 3 + ['reversal_against'] * 1
        w, gaps = ev.mirror_weights(labels)
        self.assertAlmostEqual(sum(w), 1.0)
        mass = lambda name: sum(x for x, l in zip(w, labels) if l == name)
        self.assertAlmostEqual(mass('trend_with'), mass('trend_against'))
        self.assertAlmostEqual(mass('reversal_with'), mass('reversal_against'))
        self.assertAlmostEqual(mass('trend_with') + mass('trend_against'), 10 / 16)
        self.assertEqual(gaps, [])
        w6, _ = ev.mirror_weights(labels, hit_rate=0.6)
        self.assertAlmostEqual(sum(x for x, l in zip(w6, labels) if l == 'trend_with') /
                               sum(x for x, l in zip(w6, labels) if l.startswith('trend')), 0.6)
        _, gaps = ev.mirror_weights(['trend_against', 'chop'])
        self.assertEqual(gaps, ['trend_with/trend_against'])

    def test_payoff_ratio_and_profit_factor(self):
        m = ev.weighted_metrics([1.0, -0.5, -0.5, None])
        self.assertEqual(m['n'], 3)
        self.assertAlmostEqual(m['expectancy'], 0.0)
        self.assertAlmostEqual(m['payoff_ratio'], 2.0)
        self.assertAlmostEqual(m['profit_factor'], 1.0)
        self.assertIsNone(ev.weighted_metrics([0.2, 0.3])['payoff_ratio'])

    def test_parameter_neighbours_move_each_number_both_ways_and_skip_invalid(self):
        from custody.engines.zero_dte_timing import validate_params
        from helpers import BASE_PARAMS
        moves = {(name, to) for name, _, to, _ in ev.neighbor_params(BASE_PARAMS, validate_params)}
        self.assertIn(('trail_atr', 1.875), moves)
        self.assertIn(('ema_fast', 7), moves)
        self.assertIn(('momentum_lookback', 4), moves)            # ints move by at least one
        self.assertNotIn(('trend_buffer_atr', 0.0), moves)        # zero-valued parameters are skipped
        self.assertNotIn(('flatten_before_close_minutes', 11), moves)  # below the platform minimum -> invalid
        self.assertTrue(all(validate_params(p) for _, _, _, p in ev.neighbor_params(BASE_PARAMS, validate_params)))

    def test_verdict_levels(self):
        passing = {'G1_completion_100pct': True, 'G3': True, 'G4': True}
        self.assertEqual(ev.verdict(passing, True, True, 25, True), 'ACCEPT')
        self.assertEqual(ev.verdict(passing, True, True, 5, True), 'PROVISIONAL')
        self.assertEqual(ev.verdict(passing, True, False, 25, True), 'PROVISIONAL')
        self.assertEqual(ev.verdict(passing, True, True, 25, False), 'PROVISIONAL')  # one-sided data can never be ACCEPT
        self.assertEqual(ev.verdict(dict(passing, G4=False), True, True, 25, True), 'REJECT')
        self.assertEqual(ev.verdict(dict(passing, G1_completion_100pct=False), True, True, 25, True), 'INVALID')
        self.assertEqual(ev.verdict(passing, False, True, 25, True), 'INVALID')
        self.assertEqual(ev.verdict(dict(passing, G4=None), True, True, 25, True), 'PROVISIONAL')   # cannot judge -> never ACCEPT
        self.assertEqual(ev.verdict(dict(passing, G3=None, G4=False), True, True, 25, True), 'REJECT')

    def test_tri_state_helpers(self):
        self.assertIsNone(ev._gt(None, 1.0))
        self.assertFalse(ev._gt(0.5, 1.0))
        self.assertIsNone(ev._all([True, None]))
        self.assertFalse(ev._all([None, False]))
        self.assertIsNone(ev._all([]))
        self.assertTrue(ev._all([True, True]))


class EndToEndTests(unittest.TestCase):
    def test_report_on_a_small_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = []
            for i, points in enumerate(([(1, 100.0), (15, 100.0), (390, 103.0)],
                                        [(1, 100.0), (60, 98.0), (390, 101.0)])):
                closes = piecewise(points)
                day = '2026-09-1%d' % (4 + i)
                for right, code in (('CALL', 'US.SPY26091%dC100000' % (4 + i)), ('PUT', 'US.SPY26091%dP100000' % (4 + i))):
                    cases.append({'symbol': 'US.SPY', 'contract': code, 'trade_date': day, 'underlying': closes,
                                  'option': option_bars(closes, 100, right, code, day=day)})
            write_dataset(Path(tmp) / 'ds', cases)
            report = ev.evaluate(Path(tmp) / 'ds', null_draws=20)
            self.assertEqual(report['dataset']['cases'], 4)
            self.assertEqual((report['dataset']['sides']['ok'], report['dataset']['sides']['both_sides']), (True, 2))
            self.assertEqual(report['summary']['completion_rate'], 1.0)
            self.assertTrue(report['prefix_consistency']['passed'])
            self.assertIn(report['verdict'], ('REJECT', 'PROVISIONAL'))  # never ACCEPT without OOS sessions
            self.assertEqual(set(report['summary']['by_scenario']), set(ev.SCENARIOS))
            # 2026-09-15 is after the strategy's development cutoff: one out-of-sample session.
            self.assertEqual(report['out_of_sample']['sessions'], 1)
            markdown = ev.render_markdown(report)
            for section in ('## 先看这里', '对照组', '## 1. 过没过门槛', '## 2. 总体', '## 3. 分走势看', '## 4. 结果靠不靠得住', '## 5. 逐笔明细'):
                self.assertIn(section, markdown)
            self.assertEqual(ev.evaluate(Path(tmp) / 'ds', null_draws=20), report)  # deterministic


if __name__ == '__main__':
    unittest.main()
