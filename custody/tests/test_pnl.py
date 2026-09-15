"""Option-only PnL, paired-data enforcement and paired train-builder tests.

Fixtures are the same small **real** OpenD subset as ``test_marketdata.py``; no
synthetic prices are used as a custody benchmark.
"""
import json
import tempfile
import unittest
from pathlib import Path

from custody.eval_session import eval_session
from custody.marketdata import Bar, MissingCustodyPairError, require_paired_bars
from custody.offline import OfflineMarket, assert_paired_slice
from custody.pnl import (CustodyMetricError, assert_custody_role, custody_case_pnl, option_pnl,
                         option_return, underlying_proxy_pnl)
from custody.train_slice import build_train_slice, nearest_contract

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
DAY = '2026-09-14'
CONTRACT = 'US.QQQ260914C705000'


class FakeMarket:
    """In-memory provider over the real fixtures with a listed option chain."""

    def __init__(self, underlying, option, chain):
        self.underlying, self.option, self.chain = underlying, option, chain

    def history_bars(self, code, ktype, start, end, boundary=None):
        base = self.underlying if code == 'US.QQQ' else self.option
        return [Bar(code, b.close_time, b.open, b.high, b.low, b.close, b.volume, b.interval, 'opend_kline')
                for b in base]

    def current_bars(self, code, count, ktype, boundary=None):
        return self.history_bars(code, ktype, None, None, boundary)[-count:]

    def option_chain(self, code, expiry, right=None):
        return list(self.chain)


def _slice(root, role='eval/custody', option=True):
    root = Path(root)
    (root / 'underlying').mkdir(parents=True, exist_ok=True)
    (root / 'option').mkdir(parents=True, exist_ok=True)
    series = []
    for rel, meta in [('underlying/US.QQQ.csv', {'code': 'US.QQQ', 'kind': 'underlying'}),
                      ('underlying/US.QQQ.parquet', None)]:
        (root / rel).write_bytes((FIXTURES / rel).read_bytes())
        rel2 = rel.replace('.csv', '.parquet')
        (root / rel2).write_bytes((FIXTURES / rel2).read_bytes())
        if meta:
            series.append({**meta, 'csv': rel, 'parquet': rel2, 'bar_count': 390})
    if option:
        for rel, meta in [('option/US.QQQ260914C705000.csv',
                           {'code': CONTRACT, 'kind': 'option'})]:
            (root / rel).write_bytes((FIXTURES / rel).read_bytes())
            rel2 = rel.replace('.csv', '.parquet')
            (root / rel2).write_bytes((FIXTURES / rel2).read_bytes())
            series.append({**meta, 'csv': rel, 'parquet': rel2, 'bar_count': 405})
    cases = {'schema_version': 1, 'dataset': 'custody-test', 'role': role, 'trade_date': DAY,
             'cases': [{'symbol': 'US.QQQ', 'direction': 'LONG', 'contract': CONTRACT, 'trade_date': DAY}]}
    manifest = {'schema_version': 1, 'dataset': 'custody-test', 'role': role, 'trade_date': DAY,
                'paired': option, 'required_series': ['underlying', 'option'],
                'success_metric': 'option_pnl',
                'session': {'open': DAY + 'T09:30:00-04:00', 'close': DAY + 'T16:00:00-04:00'},
                'series': series}
    (root / 'cases.json').write_text(json.dumps(cases))
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return root


class PnlTests(unittest.TestCase):
    def test_option_pnl_is_exit_minus_entry_for_both_directions(self):
        self.assertEqual(option_pnl(2.0, 2.5, qty=1), 50.0)
        self.assertEqual(option_pnl(2.0, 2.5, qty=2, multiplier=100), 100.0)
        self.assertEqual(option_pnl(2.0, 2.5, direction='LONG'), option_pnl(2.0, 2.5, direction='SHORT'))
        self.assertEqual(option_pnl(2.5, 2.0), -50.0)
        with self.assertRaises(ValueError):
            option_pnl(-1.0, 2.0)

    def test_option_return_on_premium(self):
        self.assertAlmostEqual(option_return(2.0, 2.5), 0.25)
        with self.assertRaises(ValueError):
            option_return(0.0, 2.5)

    def test_custody_case_pnl_uses_fill_prices_only(self):
        block = custody_case_pnl({'price': 2.0, 'basis': 'option_bar_close'},
                                 {'price': 2.5, 'basis': 'option_bar_close'}, direction='LONG')
        self.assertEqual(block['option_pnl'], 50.0)
        self.assertEqual(block['metric_asset'], 'option_contract')
        self.assertIsNone(custody_case_pnl(None, None)['option_pnl'])

    def test_underlying_proxy_payoff_is_hard_blocked(self):
        with self.assertRaises(CustodyMetricError):
            underlying_proxy_pnl(0.01, 100, 100)

    def test_roles(self):
        self.assertEqual(assert_custody_role('eval/custody'), 'eval/custody')
        self.assertEqual(assert_custody_role('train/custody'), 'train/custody')
        with self.assertRaises(CustodyMetricError):
            assert_custody_role('train/research')
        with self.assertRaises(CustodyMetricError):
            assert_custody_role(None)


class PairedEnforcementTests(unittest.TestCase):
    def test_require_paired_bars_rejects_missing_option(self):
        market = OfflineMarket(FIXTURES)
        with self.assertRaises(MissingCustodyPairError):
            require_paired_bars(market, 'US.QQQ', 'US.MISSING', DAY)

    def test_underlying_only_slice_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _slice(tmp, role='train/research', option=False)
            with self.assertRaises(MissingCustodyPairError):
                assert_paired_slice(root)
            with self.assertRaises(MissingCustodyPairError):
                eval_session(root)

    def test_paired_research_role_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _slice(tmp, role='train/research', option=True)
            assert_paired_slice(root)  # paired ...
            with self.assertRaises(CustodyMetricError):
                eval_session(root)     # ... but not a custody role

    def test_paired_slice_reports_option_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _slice(tmp, role='eval/custody', option=True)
            report = eval_session(root)
            self.assertEqual(report['success_metric'], 'option_pnl')
            self.assertTrue(report['underlying_proxy_metric_forbidden'])
            self.assertEqual(report['pnl_summary']['case_count'], 1)
            case = report['cases'][0]
            self.assertIn('option_pnl', case)
            self.assertEqual(case['option_pnl']['metric_asset'], 'option_contract')
            self.assertIsNotNone(case['option_pnl']['option_pnl'])
            self.assertAlmostEqual(report['pnl_summary']['total_option_pnl'],
                                   case['option_pnl']['option_pnl'])
            self.assertNotIn('underlying_pnl', json.dumps(case))


class TrainBuilderTests(unittest.TestCase):
    def test_nearest_contract_picks_closest_listed_strike(self):
        rows = [
            {'code': 'US.QQQ260918C700000', 'option_type': 'CALL', 'strike_price': 700.0},
            {'code': 'US.QQQ260918C705000', 'option_type': 'CALL', 'strike_price': 705.0},
            {'code': 'US.QQQ260918C710000', 'option_type': 'CALL', 'strike_price': 710.0},
        ]
        self.assertEqual(nearest_contract(rows, 704.0, 'CALL'), ('US.QQQ260918C705000', 705.0))
        with self.assertRaises(ValueError):
            nearest_contract(rows, 704.0, 'PUT')

    def test_build_train_slice_writes_paired_layout(self):
        real = OfflineMarket(FIXTURES)
        underlying = [b for b in real._bars('US.QQQ')]
        option = [b for b in real._bars(CONTRACT)]
        chain = [{'code': 'US.QQQ260914C705000', 'option_type': 'CALL', 'strike_price': 705.0},
                 {'code': 'US.QQQ260914P705000', 'option_type': 'PUT', 'strike_price': 705.0}]
        market = FakeMarket(underlying, option, chain)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'custody-train-test'
            manifest = build_train_slice(root, sessions=[DAY], underlyings=['US.QQQ'],
                                         expiry=DAY, market=market, logger=lambda *a, **k: None)
            self.assertEqual(manifest['role'], 'train/custody-starter')
            self.assertTrue(manifest['paired'])
            self.assertEqual(len(manifest['cases']), 1)
            self.assertEqual(manifest['cases'][0]['direction'], 'SHORT')
            self.assertEqual(manifest['cases'][0]['contract'], 'US.QQQ260914P705000')
            self.assertTrue((root / 'underlying' / 'US.QQQ.csv').exists())
            self.assertTrue((root / 'option' / 'US.QQQ260914P705000.csv').exists())
            loaded_manifest, cases = assert_paired_slice(root)
            self.assertEqual(cases['cases'][0]['contract'], 'US.QQQ260914P705000')
            report = eval_session(root)
            self.assertEqual(report['role'], 'train/custody-starter')
            self.assertIsNotNone(report['cases'][0]['option_pnl']['option_pnl'])


if __name__ == '__main__':
    unittest.main()
