import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from metrics import DayBar, score_bar  # noqa: E402
from screen import load_fixture, screen_bars  # noqa: E402

FIX = ROOT / "fixtures" / "hk_2026-09-18.json"


class TestHk20260918(unittest.TestCase):
    def test_fixture_loads(self):
        bars = load_fixture(FIX)
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0].trade_date, "2026-09-18")

    def test_minimax_is_gap_and_go(self):
        bars = {b.symbol: b for b in load_fixture(FIX)}
        s = score_bar(bars["HK.00100"])
        self.assertTrue(s.gap_and_go)
        self.assertAlmostEqual(s.auction_pct, (264.8 - 254.8) / 254.8, places=5)
        self.assertAlmostEqual(s.day_pct, (303.0 - 254.8) / 254.8, places=5)
        self.assertEqual(s.close_location, 1.0)

    def test_screen_selects_strong_auction_and_day(self):
        result = screen_bars(load_fixture(FIX))
        syms = [s.symbol for s in result.selected]
        self.assertIn("HK.00100", syms)
        self.assertIn("HK.02513", syms)
        # 阿里竞价仅 ~1.24%，默认门槛 1.5% 应落选
        self.assertNotIn("HK.09988", syms)
        self.assertEqual(result.scores[0].symbol, "HK.00100")

    def test_ali_weaker_auction(self):
        bars = {b.symbol: b for b in load_fixture(FIX)}
        s = score_bar(bars["HK.09988"])
        self.assertLess(s.auction_pct, 0.015)
        self.assertGreater(s.day_pct, 0.03)


if __name__ == "__main__":
    unittest.main()
