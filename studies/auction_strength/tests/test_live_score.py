import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from live_score import (  # noqa: E402
    FACTOR_FIELDS,
    ROW_FIELDS,
    format_live_table,
    rank_watchlist,
    score_series,
)


def _series(prices, **kw):
    base = {
        "prev_close": 100.0,
        "volume": 1e6,
        "turnover": 5e7,
        "volume_ratio": 2.0,
        "book_imbalance": 0.3,
        "circular_market_val": 1e10,
        "raw": {"name": "T"},
    }
    base.update(kw)
    out = []
    for i, px in enumerate(prices):
        row = dict(base)
        row["last_price"] = px
        row["ts"] = f"t{i}"
        out.append(row)
    return out


class TestLiveScore(unittest.TestCase):
    def test_strong_path_beats_thin_premium(self):
        strong = score_series("HK.A", _series([101, 102, 103, 104, 105]), market="HK")
        thin = score_series(
            "HK.B",
            [{"last_price": 108, "prev_close": 100}],
            market="HK",
        )
        self.assertEqual(thin.data_grade, "thin")
        self.assertLessEqual(thin.score, 40)
        self.assertIn(strong.data_grade, ("full", "partial"))
        self.assertGreater(strong.score, thin.score)

    def test_late_spike_penalized_vs_steady(self):
        steady = score_series("HK.S", _series([100, 101, 102, 103, 104, 105, 106, 107]), market="HK")
        spike = score_series("HK.X", _series([100, 100.1, 100.2, 100.3, 100.4, 100.5, 100.6, 108]), market="HK")
        self.assertIsNotNone(spike.path_late_spike)
        self.assertIsNotNone(steady.path_late_spike)
        self.assertGreater(spike.path_late_spike, steady.path_late_spike)

    def test_rank(self):
        cap = {
            "HK.00100": _series([260, 262, 265, 268, 270], volume_ratio=2.5, book_imbalance=0.4),
            "HK.09988": _series([106, 106.1, 106.2, 106.3, 106.4], volume_ratio=0.8, book_imbalance=-0.2),
        }
        ranked = rank_watchlist(cap, market="HK", names={"HK.00100": "MM"})
        self.assertEqual(ranked[0].code, "HK.00100")
        self.assertEqual(ranked[0].rank, 1)
        self.assertEqual(ranked[1].rank, 2)
        row = ranked[0].to_row()
        for key in ROW_FIELDS:
            self.assertIn(key, row)
        self.assertEqual(row["label"], ranked[0].label)
        self.assertEqual(row["data_grade"], ranked[0].data_grade)
        self.assertEqual(set(row["factors"]), set(FACTOR_FIELDS))
        table = format_live_table(ranked)
        self.assertIn("rank", table.splitlines()[0])
        self.assertIn("HK.00100", table)


if __name__ == "__main__":
    unittest.main()
