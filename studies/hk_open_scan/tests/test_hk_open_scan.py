"""Offline self-check: no futu, no OpenD, no network."""
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'code' / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bt = load('backtest')
cap = load('capture')
scan = load('scan')


def day(auction_vol=1000.0, gap=0.0, drift=0.0, prev_close=10.0):
    """One synthetic session: an auction doji, then 09:31..16:00 of drift."""
    px = prev_close * (1 + gap)
    bars = {'09:30': (px, px, px, px, auction_vol, px * auction_vol, prev_close)}
    for i in range(1, 391):
        hh, mm = divmod(30 + i, 60)
        t = '%02d:%02d' % (9 + hh, mm)
        if t > '16:00' or ('12:01' <= t <= '12:59'):
            continue
        p = px * (1 + drift * i / 390.0)
        bars[t] = (p, p * 1.001, p * 0.999, p, 1e6, p * 1e6, prev_close)
    return bars


class Session(unittest.TestCase):
    def test_the_0930_bar_is_the_auction_not_the_first_traded_minute(self):
        s = bt.session(day(auction_vol=5000.0, gap=0.03))
        self.assertAlmostEqual(s['auc_px'], 10.3)
        self.assertEqual(s['auc_vol'], 5000.0)
        self.assertAlmostEqual(s['gap'], 0.03, places=9)
        # the auction volume belongs to open_vol, and 09:31 is the first tradable price
        self.assertEqual(s['open_vol'], 5000.0 + 15 * 1e6)
        self.assertAlmostEqual(s['entry_auc'], 10.3, places=6)

    def test_a_session_missing_its_close_is_dropped(self):
        bars = day()
        del bars['15:59']
        self.assertIsNone(bt.session(bars))


class Baselines(unittest.TestCase):
    def build(self, days, tick=0.01):
        bars = {('HK.00001', d): b for d, b in days}
        return bt.build(bars, {'HK.00001': tick})

    def test_baselines_never_see_the_day_they_score(self):
        """A huge auction on the last day must not inflate its own baseline."""
        days = [('2026-09-%02d' % (d + 1), day(auction_vol=1000.0)) for d in range(15)]
        days.append(('2026-09-16', day(auction_vol=100000.0)))
        rows = self.build(days)
        self.assertAlmostEqual(rows['2026-09-16']['HK.00001']['auc_rvol'], 100.0)

    def test_a_day_without_enough_prior_days_is_not_scored(self):
        days = [('2026-09-%02d' % (d + 1), day()) for d in range(5)]
        self.assertEqual(self.build(days), {})

    def test_wide_ticks_are_filtered_before_ranking(self):
        days = [('2026-09-%02d' % (d + 1), day()) for d in range(15)]
        self.assertTrue(self.build(days, tick=0.001))       # 10bp, kept
        self.assertEqual(self.build(days, tick=0.05), {})   # 500bp, dropped


class Costs(unittest.TestCase):
    def test_cost_is_charged_both_ways_and_lowers_the_return(self):
        days = [('2026-09-%02d' % (d + 1), day(drift=0.01)) for d in range(15)]
        row = bt.build({('HK.00001', d): b for d, b in days},
                       {'HK.00001': 0.01})['2026-09-15']['HK.00001']
        self.assertAlmostEqual(row['gross'] - row['net'],
                               bt.TICKS_PER_ROUND_TRIP * 0.01 / row['entry'])


class Cadence(unittest.TestCase):
    def test_the_capture_speeds_up_across_the_matching_window(self):
        import datetime
        at = lambda h, m: datetime.datetime(2026, 9, 22, h, m)
        self.assertEqual(cap.cadence(at(8, 55)), 60)
        self.assertEqual(cap.cadence(at(9, 19)), 5)     # no-cancel, before matching
        self.assertEqual(cap.cadence(at(9, 40)), 10)    # first fifteen traded minutes
        self.assertEqual(cap.cadence(at(10, 30)), 60)


class MissingFactors(unittest.TestCase):
    def test_a_missing_baseline_is_absent_not_neutral(self):
        snap = {'HK.00001': {'code': 'HK.00001', 'name': 'x', 'prev_close_price': 10.0,
                             'last_price': 10.5, 'price_spread': 0.01, 'turnover': 1e8,
                             'volume': 1e6, 'avg_price': 10.4, 'volume_ratio': 2.0,
                             'turnover_rate': 1.0, 'bid_vol': 100.0, 'ask_vol': 50.0,
                             'sec_status': 'NORMAL'}}
        rows = scan.score_rows(snap, {'HK.00001': {'day_turnover': 1e9}}, 'open15')
        self.assertIsNone(rows[0]['rvol'])                 # absent, not neutral
        self.assertIn('open15_turnover', rows[0]['no_baseline'])
        self.assertEqual(rows[0]['excluded'], [])          # liquidity is known and fine
        self.assertEqual(len(scan.rank(rows, 'auction')[0]), 1)

    def test_unknown_liquidity_keeps_a_name_off_the_board(self):
        snap = {'HK.00002': {'code': 'HK.00002', 'name': 'y', 'prev_close_price': 10.0,
                             'last_price': 10.5, 'price_spread': 0.01, 'turnover': 1e8,
                             'volume': 1e6, 'avg_price': 10.4, 'volume_ratio': 2.0,
                             'turnover_rate': 1.0, 'bid_vol': 100.0, 'ask_vol': 50.0,
                             'sec_status': 'NORMAL'}}
        rows = scan.score_rows(snap, {}, 'auction')
        self.assertEqual(rows[0]['excluded'], ['no_liquidity_baseline'])
        scored, off = scan.rank(rows, 'auction')
        self.assertEqual(scored, [])
        self.assertEqual(len(off), 1)


class ReadOnly(unittest.TestCase):
    def test_the_directory_never_touches_the_trade_api(self):
        forbidden = ('OpenSecTradeContext', 'place_order', 'unlock_trade', 'modify_order')
        for path in (ROOT / 'code').rglob('*.py'):
            text = path.read_text(encoding='utf-8')
            for word in forbidden:
                self.assertNotIn(word, text, '%s mentions %s' % (path, word))


if __name__ == '__main__':
    unittest.main(verbosity=2)
