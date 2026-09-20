import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from run_auction_pulse import (  # noqa: E402
    load_watchlist,
    main,
    merge_universe,
    parse_code_list,
)

CAPTURE = ROOT / "fixtures" / "live_capture_hk.json"
WATCH_HK = ROOT / "config" / "watchlist_hk.json"


class TestCliUniverse(unittest.TestCase):
    def test_parse_codes_normalizes(self):
        self.assertEqual(parse_code_list("00100,09988"), ["HK.00100", "HK.09988"])
        self.assertEqual(parse_code_list("HK.00100 HK.01810"), ["HK.00100", "HK.01810"])
        self.assertEqual(parse_code_list("688327"), ["SH.688327"])
        self.assertEqual(parse_code_list(""), [])

    def test_watchlist_and_codes_filter(self):
        codes, names = load_watchlist(WATCH_HK)
        self.assertIn("HK.00100", codes)
        self.assertEqual(names["HK.00100"], "MiniMax")
        filtered, fnames = merge_universe(codes, names, ["HK.00100", "HK.09988"])
        self.assertEqual(filtered, ["HK.00100", "HK.09988"])
        self.assertEqual(fnames["HK.00100"], "MiniMax")
        self.assertNotIn("HK.01810", filtered)


class TestPulseFromCapture(unittest.TestCase):
    def _run(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(argv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_from_capture_table_and_json(self):
        rc, out, _err = self._run(
            ["--market", "HK", "--from-capture", str(CAPTURE)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("rank", out)
        self.assertIn("HK.00100", out)
        payload = json.loads(out[out.index("{") :])
        self.assertEqual(payload["market"], "HK")
        rows = payload["rows"]
        self.assertGreaterEqual(len(rows), 3)
        self.assertEqual(rows[0]["rank"], 1)
        for row in rows:
            self.assertIn("rank", row)
            self.assertIn("score", row)
            self.assertIn("label", row)
            self.assertIn("data_grade", row)
            self.assertIn("factors", row)
            self.assertIn("book_imbalance", row["factors"])
            self.assertIn("volume_ratio", row["factors"])
            self.assertIn("path_late_spike", row["factors"])
        by_code = {r["code"]: r for r in rows}
        self.assertEqual(by_code["HK.09988"]["data_grade"], "thin")
        self.assertLessEqual(by_code["HK.09988"]["score"], 40)
        self.assertLess(by_code["HK.09988"]["rank"], 99)
        self.assertGreater(by_code["HK.00100"]["score"], by_code["HK.09988"]["score"])
        self.assertEqual(rows[0]["code"], "HK.00100")

    def test_codes_filter_capture(self):
        rc, out, _err = self._run(
            [
                "--market",
                "HK",
                "--from-capture",
                str(CAPTURE),
                "--codes",
                "09988,00100",
            ]
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out[out.index("{") :])
        codes = [r["code"] for r in payload["rows"]]
        self.assertEqual(set(codes), {"HK.00100", "HK.09988"})
        self.assertNotIn("HK.02513", codes)

    def test_out_json_has_rank_and_series(self):
        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "pulse.json"
            rc, _out, err = self._run(
                [
                    "--market",
                    "HK",
                    "--from-capture",
                    str(CAPTURE),
                    "--watchlist",
                    str(WATCH_HK),
                    "--out",
                    str(out_path),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertIn("wrote", err)
            saved = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertIn("series", saved)
            self.assertIn("rows", saved)
            self.assertEqual(saved["rows"][0]["rank"], 1)
            self.assertEqual(saved["scores"][0]["label"], saved["rows"][0]["label"])
            self.assertIn("data_grade", saved["rows"][0])
            self.assertIn("factors", saved["rows"][0])
            self.assertEqual(saved["rows"][0]["name"], "MiniMax")


if __name__ == "__main__":
    unittest.main()
