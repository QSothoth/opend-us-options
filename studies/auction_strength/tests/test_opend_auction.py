import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from opend_auction import (  # noqa: E402
    QuoteSession,
    fetch_snapshot_ticks,
    sample_window,
)


class FakeRow(dict):
    def to_dict(self):
        return dict(self)


class FakeFrame:
    def __init__(self, rows):
        self._rows = [FakeRow(r) for r in rows]

    def iterrows(self):
        for i, row in enumerate(self._rows):
            yield i, row


class FakeFt:
    RET_OK = 0
    RET_ERROR = -1

    class SubType:
        ORDER_BOOK = "ORDER_BOOK"
        QUOTE = "QUOTE"


class FakeQuoteContext:
    def __init__(self):
        self.close_calls = 0
        self.snapshot_calls = 0
        self.subscribe_calls = 0
        self.book_calls = 0
        self.closed = False

    def get_market_state(self, codes):
        return FakeFt.RET_OK, FakeFrame(
            [{"code": c, "market_state": "AUCTION"} for c in codes]
        )

    def subscribe(self, codes, subtypes):
        self.subscribe_calls += 1
        return FakeFt.RET_OK, None

    def get_market_snapshot(self, codes):
        if self.closed:
            raise RuntimeError("snapshot on closed context")
        self.snapshot_calls += 1
        rows = []
        for code in codes:
            rows.append(
                {
                    "code": code,
                    "last_price": 100.0 + self.snapshot_calls,
                    "open_price": 100.0,
                    "prev_close_price": 100.0,
                    "volume": 1000 * self.snapshot_calls,
                    "turnover": 1e5 * self.snapshot_calls,
                    "bid_price": 100.0,
                    "ask_price": 100.2,
                    "bid_vol": 20.0,
                    "ask_vol": 10.0,
                    "bid_ask_ratio": 2.0,
                    "volume_ratio": 1.5,
                    "outstanding_shares": 1e9,
                    "circular_market_val": 1e10,
                    "name": "T",
                    "update_time": "09:19:00",
                    "turnover_rate": 0.1,
                    "amplitude": 0.02,
                    "sec_status": "NORMAL",
                }
            )
        return FakeFt.RET_OK, FakeFrame(rows)

    def get_order_book(self, code, num=10):
        self.book_calls += 1
        return FakeFt.RET_OK, {
            "Bid": [(100.0, 50.0), (99.9, 30.0)],
            "Ask": [(100.2, 20.0), (100.3, 10.0)],
        }

    def close(self):
        self.close_calls += 1
        self.closed = True


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def time(self):
        return self.t

    def sleep(self, seconds):
        self.t += float(seconds)


class TestQuoteReuse(unittest.TestCase):
    def test_fetch_does_not_close_injected_ctx(self):
        ctx = FakeQuoteContext()
        ticks = fetch_snapshot_ticks(
            ["HK.00100"],
            ctx=ctx,
            ft=FakeFt(),
            sleep=lambda _s: None,
        )
        self.assertEqual(len(ticks), 1)
        self.assertEqual(ticks[0].code, "HK.00100")
        self.assertIsNotNone(ticks[0].book_imbalance)
        self.assertEqual(ctx.close_calls, 0)
        self.assertEqual(ctx.snapshot_calls, 1)

    def test_sample_window_reuses_one_connection(self):
        ctx = FakeQuoteContext()
        clock = FakeClock()
        series = sample_window(
            ["HK.00100", "HK.09988"],
            duration_sec=0.05,
            interval_sec=0.02,
            ctx=ctx,
            ft=FakeFt(),
            clock=clock.time,
            sleep=clock.sleep,
        )
        self.assertGreaterEqual(ctx.snapshot_calls, 2)
        self.assertEqual(ctx.subscribe_calls, 1)
        self.assertEqual(ctx.close_calls, 0)  # caller owns ctx
        self.assertEqual(len(series["HK.00100"]), ctx.snapshot_calls)
        self.assertTrue(all(row["book_imbalance"] is not None for row in series["HK.00100"]))

    def test_sample_window_owned_connect_closes_once(self):
        ctx = FakeQuoteContext()
        clock = FakeClock()

        def connect(host, port):
            return FakeFt(), ctx

        with patch("opend_auction._connect", side_effect=connect):
            series = sample_window(
                ["HK.00100"],
                duration_sec=0.04,
                interval_sec=0.02,
                clock=clock.time,
                sleep=clock.sleep,
            )
        self.assertGreaterEqual(ctx.snapshot_calls, 2)
        self.assertEqual(ctx.subscribe_calls, 1)
        self.assertEqual(ctx.close_calls, 1)
        self.assertGreaterEqual(len(series["HK.00100"]), 2)

    def test_standalone_fetch_closes_owned_connect(self):
        ctx = FakeQuoteContext()

        def connect(host, port):
            return FakeFt(), ctx

        with patch("opend_auction._connect", side_effect=connect):
            ticks = fetch_snapshot_ticks(["HK.00100"], sleep=lambda _s: None)
        self.assertEqual(len(ticks), 1)
        self.assertEqual(ctx.close_calls, 1)
        self.assertEqual(ctx.subscribe_calls, 1)

    def test_quote_session_rejects_trade_context(self):
        class FakeTradeContext:
            pass

        with self.assertRaises(RuntimeError):
            with QuoteSession(ctx=FakeTradeContext(), ft=FakeFt()):
                pass

    def test_duration_zero_still_samples_once(self):
        ctx = FakeQuoteContext()
        clock = FakeClock()
        series = sample_window(
            ["HK.00100"],
            duration_sec=0.0,
            interval_sec=1.0,
            ctx=ctx,
            ft=FakeFt(),
            clock=clock.time,
            sleep=clock.sleep,
        )
        self.assertEqual(ctx.snapshot_calls, 1)
        self.assertEqual(len(series["HK.00100"]), 1)


class TestReadOnlySources(unittest.TestCase):
    def test_code_has_no_trade_api_calls(self):
        forbidden = ("OpenSecTradeContext", "place_order", "unlock_trade", "modify_order")
        for path in (ROOT / "code").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for name in forbidden:
                self.assertNotIn(name, text, f"{path.name} must not mention {name}")


if __name__ == "__main__":
    unittest.main()
