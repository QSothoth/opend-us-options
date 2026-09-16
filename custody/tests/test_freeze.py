import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from helpers import DAY, option_bars, path_bars, piecewise, session  # noqa: E402

from custody.dataset import Dataset  # noqa: E402
from custody.freeze import RateLimiter, freeze_session  # noqa: E402
from custody.models import ET  # noqa: E402

AFTER_CLOSE = datetime(2026, 9, 14, 16, 30, tzinfo=ET)


class Calendar:
    def session(self, day):
        return session(day)


class FakeMarket:
    """Read-only surface used by freeze_session; SPY has a 0DTE chain, AAPL does not."""

    def __init__(self):
        self.closes = piecewise([(1, 600.2), (390, 603.0)])
        self.calls = []

    def history_bars(self, code, ktype, start, end, boundary=None):
        self.calls.append((code, ktype))
        if code in ('US.SPY', 'US.AAPL'):
            return path_bars(self.closes, code)
        right = 'CALL' if 'C6' in code else 'PUT'
        return option_bars(self.closes, 600, right, code)

    def option_chain(self, code, expiry):
        if code != 'US.SPY':
            return []
        return [{'code': 'US.SPY260914%s%06d' % (r, k * 1000), 'strike_price': k} for k in (599, 600, 601) for r in 'CP']

    def request_kline(self, code, ktype, start, end):
        return [{'time_key': '2026-09-11', 'open': 598, 'high': 601, 'low': 597, 'close': 599.5},
                {'time_key': DAY, 'open': 600, 'high': 604, 'low': 599, 'close': 603}]


class FreezeTests(unittest.TestCase):
    def test_freezes_both_sides_with_prev_close_and_verifiable_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'work'
            market = FakeMarket()
            summary = freeze_session(market, Calendar(), root, DAY, ['US.SPY', 'US.AAPL'], now=AFTER_CLOSE,
                                     limiter=RateLimiter(calls=1000), log=lambda *a: None)
            self.assertEqual(summary['added_cases'], ['US.SPY260914C600000', 'US.SPY260914P600000'])
            self.assertEqual(summary['skipped'][0]['symbol'], 'US.AAPL')
            ds = Dataset(root)
            self.assertEqual([(c.direction, c.prev_close, c.selection) for c in ds.cases],
                             [('LONG', 599.5, 'both_sides_atm_at_open'), ('SHORT', 599.5, 'both_sides_atm_at_open')])
            self.assertEqual(len(ds.load(ds.cases[1]).underlying), 390)
            manifest = json.loads((root / 'manifest.json').read_text())
            self.assertEqual((manifest['sessions'], manifest['case_count']), ([DAY], 2))
            again = freeze_session(market, Calendar(), root, DAY, ['US.SPY'], now=AFTER_CLOSE,
                                   limiter=RateLimiter(calls=1000), log=lambda *a: None)
            self.assertEqual(again['added_cases'], [])
            self.assertEqual(len(Dataset(root).cases), 2)

    def test_refuses_to_freeze_before_bars_are_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'after the session close'):
                freeze_session(FakeMarket(), Calendar(), Path(tmp), DAY, ['US.SPY'],
                               now=AFTER_CLOSE - timedelta(minutes=20), log=lambda *a: None)

    def test_rate_limiter_waits_for_the_window(self):
        clock = [0.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
        limiter = RateLimiter(calls=2, window=30.0, clock=lambda: clock[0], sleep=sleep)
        for _ in range(3):
            limiter.acquire()
        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(clock[0], 30.0)


if __name__ == '__main__':
    unittest.main()
