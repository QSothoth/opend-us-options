"""Expanded windowed custody train builder tests (offline, real-data fixtures).

The fixtures are the small **real** OpenD subset under ``custody/tests/fixtures``.
The builder's v2 input is written in-memory from those real bars so no network
or synthetic price is used.
"""
import tempfile
import unittest
from pathlib import Path

from custody.marketdata import Bar
from custody.offline import OfflineMarket, assert_paired_slice
from custody.train_window import (build_v2window_train_slice, candidate_strikes,
                                  direction_basis, direction_from_labels, expiry_candidates,
                                  load_v2_underlying, option_code)

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
DAY = '2026-09-14'
CONTRACT = 'US.QQQ260914C705000'


def _read_fixture(path):
    market = OfflineMarket(FIXTURES)
    code = Path(path).stem.upper()
    return market._bars(code)


class FakeWindowMarket:
    """In-memory provider: fixed chain + per-code option bars."""

    def __init__(self, option_bars, chain):
        self.option_bars = {code.upper(): bars for code, bars in option_bars.items()}
        self.chain = chain

    def option_chain(self, code, expiry, right=None):
        return list(self.chain)

    def history_bars(self, code, ktype, start, end, boundary=None):
        return list(self.option_bars.get(str(code).upper(), []))


def _v2_dir(tmp, bars, labels):
    import pandas as pd
    root = Path(tmp) / 'eval-data-v2'
    root.mkdir(parents=True, exist_ok=True)
    import datetime
    epoch = (pd.Series(pd.to_datetime([b.close_time for b in bars])).dt.tz_localize(None)
             - pd.Timestamp('1970-01-01')) // pd.Timedelta('1s')
    frame = pd.DataFrame({
        'symbol': ['US.QQQ'] * len(bars), 'ktype': ['K_1M'] * len(bars),
        'time': epoch.tolist(), 'open': [b.open for b in bars], 'high': [b.high for b in bars],
        'low': [b.low for b in bars], 'close': [b.close for b in bars],
        'volume': [b.volume for b in bars], 'turnover': [0.0] * len(bars),
    })
    frame.to_parquet(root / 'klines_1m.parquet', index=False)
    labels_frame = pd.DataFrame([{
        'symbol': 'US.QQQ', 'grain': 'session_1m', 'date': DAY,
        **{name: False for name in ('strong_up_trend', 'strong_down_trend', 'range_chop',
                                    'gap_up_open', 'gap_down_open', 'v_reversal_up',
                                    'v_reversal_down', 'high_vol', 'low_vol', 'late_day_spike')},
        'strong_up_trend': labels.get('strong_up_trend', False),
        'strong_down_trend': labels.get('strong_down_trend', False),
    }])
    labels_frame.to_parquet(root / 'scenario_labels.parquet', index=False)
    return root


class DirectionPolicyTests(unittest.TestCase):
    def test_bearish_labels_pick_short(self):
        self.assertEqual(direction_from_labels({'strong_down_trend': True}), 'SHORT')
        self.assertEqual(direction_from_labels({'gap_down_open': True}), 'SHORT')

    def test_bullish_labels_pick_long(self):
        self.assertEqual(direction_from_labels({'strong_up_trend': True}), 'LONG')
        self.assertEqual(direction_from_labels({'v_reversal_up': True}), 'LONG')

    def test_unlabelled_defaults_long(self):
        self.assertEqual(direction_from_labels({}), 'LONG')
        self.assertEqual(direction_from_labels(None), 'LONG')
        self.assertEqual(direction_basis({}), ['default_long'])


class ContractSelectionTests(unittest.TestCase):
    def test_option_code_round_trip(self):
        self.assertEqual(option_code('US.QQQ', '2026-09-14', 'CALL', 705.0), CONTRACT)
        self.assertEqual(option_code('US.SPY', '2026-08-21', 'PUT', 765.0), 'US.SPY260821P765000')
        self.assertEqual(option_code('US.AAPL', '2026-09-18', 'CALL', 317.5), 'US.AAPL260918C317500')

    def test_candidate_strikes_nearest_first_and_capped(self):
        chain = [{'strike_price': 700.0}, {'strike_price': 705.0}, {'strike_price': 710.0}]
        ordered = candidate_strikes(704.5, chain, max_moneyness=0.02)
        self.assertIn(705.0, ordered[:3])
        self.assertTrue(all(abs(s - 704.5) / 704.5 <= 0.02 for s in ordered))

    def test_expiry_candidates_nearest_first(self):
        self.assertEqual(expiry_candidates('2026-05-14')[0], '2026-08-21')
        self.assertEqual(expiry_candidates('2026-09-08')[0], '2026-09-08')
        self.assertIn('2026-09-18', expiry_candidates('2026-05-14'))


class WindowedBuilderTests(unittest.TestCase):
    def test_build_writes_paired_windowed_layout(self):
        underlying = _read_fixture('underlying/US.QQQ.parquet')
        option = _read_fixture('option/US.QQQ260914C705000.parquet')
        chain = [
            {'code': 'US.QQQ260914C705000', 'option_type': 'CALL', 'strike_price': 705.0},
            {'code': 'US.QQQ260914P705000', 'option_type': 'PUT', 'strike_price': 705.0},
        ]
        market = FakeWindowMarket({CONTRACT: option}, chain)
        with tempfile.TemporaryDirectory() as tmp:
            v2 = _v2_dir(tmp, underlying, {'strong_up_trend': True})
            loaded = load_v2_underlying(v2, ['US.QQQ'])
            self.assertEqual(len(loaded['US.QQQ'][DAY]), 390)
            self.assertEqual(loaded['US.QQQ'][DAY][0].close_time.strftime('%H:%M'), '09:31')
            root = Path(tmp) / 'custody-train-v2window-paired'
            manifest = build_v2window_train_slice(
                root, v2_dir=v2, start=DAY, end=DAY, underlyings=['US.QQQ'],
                market=market, logger=lambda *a, **k: None, rate_limit=False)
            self.assertEqual(manifest['role'], 'train/custody')
            self.assertEqual(manifest['case_count'], 1)
            case = manifest['cases'][0]
            self.assertEqual(case['direction'], 'LONG')
            self.assertEqual(case['contract'], CONTRACT)
            self.assertEqual(case['expiry'], DAY)
            self.assertEqual(case['dte'], 0)
            self.assertTrue((root / 'underlying' / 'US.QQQ.parquet').exists())
            self.assertTrue((root / 'option' / (CONTRACT + '.parquet')).exists())
            paired_manifest, cases = assert_paired_slice(root)
            self.assertEqual(cases['cases'][0]['contract'], CONTRACT)
            self.assertTrue(paired_manifest['paired'])


if __name__ == '__main__':
    unittest.main()
