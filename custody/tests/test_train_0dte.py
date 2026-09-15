"""True-0DTE custody train builder tests (offline, in-memory bars).

These tests use no network and no synthetic *performance* claims: the fake
provider only returns schema-valid bars so the builder's DTE gate, volume rule
and paired layout can be exercised deterministically.
"""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from custody.marketdata import Bar
from custody.models import ET
from custody.offline import assert_paired_slice
from custody.train_0dte import (apply_spy_share_cap, build_0dte_train_slice,
                                derive_single_name_floor, derive_volume_floor,
                                pooled_median, zero_dte_targets)

DAY = '2026-09-14'
OTHER = '2026-09-11'


def _bars(code, day, volume, base=5.0):
    """Return 390 schema-valid bars whose total day-volume is ``volume``."""
    start = datetime.fromisoformat(day + 'T09:31:00').replace(tzinfo=ET)
    per_bar = float(volume) / 390.0
    out = []
    for index in range(390):
        when = start + timedelta(minutes=index)
        price = base + index * 0.001
        out.append(Bar(code, when, price, price + 0.05, price - 0.05, price,
                       per_bar, '1m', 'fake'))
    return out


class FakeMarket:
    """In-memory provider keyed by exact contract code."""

    def __init__(self, bars_by_code):
        self._bars = {code.upper(): bars for code, bars in bars_by_code.items()}

    def history_bars(self, code, ktype, start, end, boundary=None):
        return list(self._bars.get(str(code).upper(), []))


class FloorTests(unittest.TestCase):
    def test_floor_is_fraction_of_median_rounded_down(self):
        self.assertEqual(derive_volume_floor([100000, 200000, 300000]), 20000)
        self.assertEqual(pooled_median([100000, 200000, 300000]), 200000)

    def test_floor_has_safe_minimum(self):
        self.assertEqual(derive_volume_floor([500, 1000]), 1000)
        self.assertIsNone(derive_volume_floor([]))

    def test_single_name_floor_is_tenth_of_index_floor(self):
        self.assertEqual(derive_single_name_floor(57000), 5000)
        self.assertEqual(derive_single_name_floor(10000), 1000)
        # never below the documented minimum
        self.assertEqual(derive_single_name_floor(0), 1000)


class ShareCapTests(unittest.TestCase):
    @staticmethod
    def _case(symbol, day, volume):
        return {'symbol': symbol, 'trade_date': day, 'contract': symbol + day,
                'chosen_volume': float(volume)}

    def test_spy_cap_keeps_highest_volume_spy_sessions(self):
        cases = [self._case('US.SPY', '2026-09-0%d' % d, 1000 * (10 - d))
                 for d in range(1, 6)]
        cases += [self._case('US.AAPL', '2026-09-0%d' % d, 100) for d in range(1, 4)]
        kept, dropped = apply_spy_share_cap(cases, cap=0.35)
        # 3 single names => keep <= 0.35*3/0.65 = 1.6 => 1 SPY
        kept_spy = [c for c in kept if c['symbol'] == 'US.SPY']
        self.assertEqual(len(kept_spy), 1)
        self.assertEqual(kept_spy[0]['chosen_volume'], 9000.0)
        self.assertEqual(len(dropped), 4)

    def test_spy_cap_is_noop_without_other_underlyings(self):
        cases = [self._case('US.SPY', '2026-09-01', 1000)]
        kept, dropped = apply_spy_share_cap(cases, cap=0.35)
        self.assertEqual(kept, cases)
        self.assertEqual(dropped, [])

    def test_spy_cap_is_noop_when_already_under_cap(self):
        cases = [self._case('US.SPY', '2026-09-01', 1000)]
        cases += [self._case('US.AAPL', '2026-09-0%d' % d, 500) for d in range(1, 5)]
        kept, dropped = apply_spy_share_cap(cases, cap=0.35)
        self.assertEqual(len(kept), 5)
        self.assertEqual(dropped, [])


class TargetTests(unittest.TestCase):
    def test_priority_order_and_window(self):
        warm = {('US.IWM', DAY), ('US.SPY', DAY), ('US.QQQ', DAY),
                ('US.SPY', '2026-07-01'), ('US.AAPL', DAY)}
        targets = zero_dte_targets(warm, ['US.SPY', 'US.QQQ', 'US.IWM', 'US.AAPL'],
                                   '2026-08-17', '2026-09-14')
        self.assertEqual(targets, [('US.SPY', DAY), ('US.QQQ', DAY),
                                   ('US.IWM', DAY), ('US.AAPL', DAY)])


class BuilderTests(unittest.TestCase):
    def _market(self, call_volume, put_volume):
        underlying = 'US.SPY'
        return FakeMarket({
            underlying: _bars(underlying, DAY, 100000, base=705.0),
            'US.SPY260914C705000': _bars('US.SPY260914C705000', DAY, call_volume),
            'US.SPY260914P705000': _bars('US.SPY260914P705000', DAY, put_volume),
        })

    def test_builds_true_0dte_paired_layout_and_picks_higher_volume(self):
        market = self._market(call_volume=9000, put_volume=1000)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'custody-train-0dte'
            manifest = build_0dte_train_slice(
                root, v2_dir=None, start=DAY, end=DAY, symbols=['US.SPY'],
                market=market, warm_groups={('US.SPY', DAY)}, volume_floor=1000,
                logger=lambda *a, **k: None)
            self.assertEqual(manifest['dataset'], 'custody-train-0dte')
            self.assertEqual(manifest['role'], 'train/custody')
            self.assertEqual(manifest['max_dte'], 0)
            self.assertEqual(manifest['case_count'], 1)
            case = manifest['cases'][0]
            self.assertEqual(case['expiry'], DAY)
            self.assertEqual(case['dte'], 0)
            self.assertEqual(case['right'], 'CALL')
            self.assertEqual(case['direction'], 'LONG')
            self.assertEqual(case['contract'], 'US.SPY260914C705000')
            self.assertAlmostEqual(case['chosen_volume'], 9000.0, places=3)
            self.assertTrue((root / 'underlying' / 'US.SPY.parquet').exists())
            self.assertTrue((root / 'option' / 'US.SPY260914C705000.parquet').exists())
            assert_paired_slice(root)

    def test_put_wins_when_put_volume_higher(self):
        market = self._market(call_volume=1000, put_volume=9000)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'custody-train-0dte'
            manifest = build_0dte_train_slice(
                root, v2_dir=None, start=DAY, end=DAY, symbols=['US.SPY'],
                market=market, warm_groups={('US.SPY', DAY)}, volume_floor=1000,
                logger=lambda *a, **k: None)
            case = manifest['cases'][0]
            self.assertEqual(case['right'], 'PUT')
            self.assertEqual(case['direction'], 'SHORT')
            self.assertEqual(case['contract'], 'US.SPY260914P705000')

    def test_below_floor_case_is_dropped(self):
        market = self._market(call_volume=9000, put_volume=1000)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'custody-train-0dte'
            manifest = build_0dte_train_slice(
                root, v2_dir=None, start=DAY, end=DAY, symbols=['US.SPY'],
                market=market, warm_groups={('US.SPY', DAY)}, volume_floor=10000,
                logger=lambda *a, **k: None)
            self.assertEqual(manifest['case_count'], 0)
            self.assertEqual(len(manifest['filtered_below_floor']), 1)

    def test_optional_symbol_below_auto_floor_is_dropped(self):
        market = FakeMarket({
            'US.SPY': _bars('US.SPY', DAY, 100000, base=705.0),
            'US.SPY260914C705000': _bars('US.SPY260914C705000', DAY, 100000),
            'US.SPY260914P705000': _bars('US.SPY260914P705000', DAY, 90000),
            'US.AAPL': _bars('US.AAPL', DAY, 100000, base=328.0),
            'US.AAPL260914C328000': _bars('US.AAPL260914C328000', DAY, 500),
            'US.AAPL260914P328000': _bars('US.AAPL260914P328000', DAY, 400),
        })
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'custody-train-0dte'
            manifest = build_0dte_train_slice(
                root, v2_dir=None, start=DAY, end=DAY, symbols=['US.SPY', 'US.AAPL'],
                market=market, warm_groups={('US.SPY', DAY), ('US.AAPL', DAY)},
                volume_floor=None, spy_share_cap=None, logger=lambda *a, **k: None)
            # Auto index floor from SPY (median 100000 -> 10000); single floor 1000.
            self.assertEqual(manifest['volume_floor'], 10000)
            self.assertEqual(manifest['volume_floor_single'], 1000)
            self.assertEqual(manifest['case_count'], 1)
            self.assertEqual(manifest['cases'][0]['symbol'], 'US.SPY')
            self.assertEqual(len(manifest['filtered_below_floor']), 1)

    def test_single_name_kept_at_lower_tier_floor(self):
        market = FakeMarket({
            'US.SPY': _bars('US.SPY', DAY, 100000, base=705.0),
            'US.SPY260914C705000': _bars('US.SPY260914C705000', DAY, 100000),
            'US.SPY260914P705000': _bars('US.SPY260914P705000', DAY, 90000),
            'US.META': _bars('US.META', DAY, 100000, base=548.0),
            'US.META260914C548000': _bars('US.META260914C548000', DAY, 5000),
            'US.META260914P548000': _bars('US.META260914P548000', DAY, 4000),
        })
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'custody-train-0dte'
            manifest = build_0dte_train_slice(
                root, v2_dir=None, start=DAY, end=DAY, symbols=['US.SPY', 'US.META'],
                market=market, warm_groups={('US.SPY', DAY), ('US.META', DAY)},
                volume_floor=None, spy_share_cap=None, logger=lambda *a, **k: None)
            # META (5000) is below the 10000 index floor but clears the 1000
            # single-name floor, so the Mag7 case survives the two-tier rule.
            self.assertEqual(manifest['volume_floor'], 10000)
            self.assertEqual(manifest['volume_floor_single'], 1000)
            self.assertEqual(manifest['case_count'], 2)
            self.assertIn('US.META', manifest['underlyings'])
            self.assertIn('US.SPY', manifest['underlyings'])


if __name__ == '__main__':
    unittest.main()
