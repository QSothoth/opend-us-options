"""Shared provider, frozen-slice and eval-session tests (offline, real-data fixtures).

Fixtures under ``custody/tests/fixtures/`` are a tiny real subset of the
2026-09-14 custody eval slice captured from read-only OpenD. They are small
enough to commit and are **real prices, not synthetic invented bars**.
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from custody.eval_session import DeadlineFallbackPolicy, eval_session
from custody.eval_slice import fetch_slice
from custody.marketdata import Bar, MarketDataProvider, day_bars, normalize_bar_rows
from custody.models import ET
from custody.offline import OfflineMarket, load_cases, load_manifest, read_bar_file
from custody.opend import OpenDMarket

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
DAY = '2026-09-14'
T = datetime(2026, 9, 14, 10, 0, tzinfo=ET)


def raw(when, close, volume=10):
    return {'time_key': when, 'open': close, 'high': close, 'low': close, 'close': close, 'volume': volume}


class BarTests(unittest.TestCase):
    def test_normalize_filters_dedupes_and_orders(self):
        rows = [
            raw('2026-09-14 09:32:00', 1.2),
            raw('2026-09-14 09:31:00', 1.1),
            raw('2026-09-14 09:31:00', 9.9),          # duplicate -> last wins
            raw('2026-09-14 09:33:00', 1.3),
            {'time_key': '2026-09-14 09:34:00', 'open': 1, 'high': 1, 'low': 1, 'close': 1},  # no volume
            {'time_key': 'N/A', 'open': 1, 'high': 1, 'low': 1, 'close': 1, 'volume': 1},
        ]
        bars = normalize_bar_rows(rows, 'US.QQQ', boundary=datetime(2026, 9, 14, 9, 32, tzinfo=ET))
        self.assertEqual([b.close_time.strftime('%H:%M') for b in bars], ['09:31', '09:32'])
        self.assertEqual(bars[0].close, 9.9)

    def test_bar_validates_ohlc_and_finiteness(self):
        with self.assertRaises(ValueError):
            Bar('US.QQQ', T, 1.0, 1.0, 2.0, 1.0, 1)  # low > open/close
        with self.assertRaises(ValueError):
            Bar('US.QQQ', T, 1.0, 2.0, 0.5, float('nan'), 1)

    def test_bar_record_matches_feature_worker_shape(self):
        bar = Bar('US.QQQ', T, 1.0, 2.0, 0.5, 1.5, 3)
        self.assertEqual(set(bar.to_record()), {'close_time', 'open', 'high', 'low', 'close', 'volume'})


class ProtocolTests(unittest.TestCase):
    def test_opend_market_has_shared_surface(self):
        market = OpenDMarket(quote_context=object())
        for name in ('quote', 'underlying_mark', 'history_bars', 'current_bars'):
            self.assertTrue(callable(getattr(market, name)), name)

    def test_opend_market_history_bars_return_shared_bar_type(self):
        rows = [raw('2026-09-14 09:31:00', 1.5), raw('2026-09-14 09:32:00', 1.6)]
        market = OpenDMarket(quote_context=FakeLiveContext(rows))
        bars = day_bars(market, 'US.QQQ', DAY)
        self.assertIsInstance(bars[0], Bar)
        self.assertEqual(bars[0].source, 'opend_kline')
        self.assertEqual([b.close for b in bars], [1.5, 1.6])

    def test_offline_market_is_a_marketdata_provider(self):
        market = OfflineMarket(FIXTURES)
        self.assertIsInstance(market, MarketDataProvider)


class FakeLiveContext:
    """Minimal stand-in for futu.OpenQuoteContext history only (no trade API)."""

    def __init__(self, rows):
        self.rows = rows

    def request_history_kline(self, code, start=None, end=None, ktype=None, autype=None,
                              max_count=None, page_req_key=None):
        return 0, _Rows(self.rows), None


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def to_dict(self, orient='records'):
        return list(self._rows)


class OfflineMarketTests(unittest.TestCase):
    def setUp(self):
        self.market = OfflineMarket(FIXTURES)

    def test_day_bars_reads_real_underlying_fixture(self):
        bars = day_bars(self.market, 'US.QQQ', DAY)
        self.assertGreater(bars[0].volume, 0)
        self.assertEqual(bars[0].code, 'US.QQQ')
        self.assertEqual(bars[0].source, 'frozen')
        self.assertTrue(all(bars[i].close_time < bars[i + 1].close_time for i in range(len(bars) - 1)))

    def test_history_bars_range_and_boundary_filter(self):
        bars = self.market.history_bars('US.QQQ', 'K_1M', '2026-09-14 09:31:00', '2026-09-14 09:35:00')
        self.assertTrue(bars)
        self.assertLessEqual(bars[-1].close_time, datetime(2026, 9, 14, 9, 35, tzinfo=ET))
        limited = self.market.history_bars('US.QQQ', 'K_1M', None, None,
                                           boundary=datetime(2026, 9, 14, 9, 32, tzinfo=ET))
        self.assertEqual(limited[-1].close_time.strftime('%H:%M'), '09:32')

    def test_non_1m_returns_empty_without_fabricating(self):
        with self.assertLogs('custody.offline', level='WARNING'):
            self.assertEqual(self.market.history_bars('US.QQQ', 'K_DAY', None, None), [])

    def test_underlying_mark_is_last_real_close(self):
        mark = self.market.underlying_mark('QQQ', T)
        self.assertIsNotNone(mark)
        self.assertGreater(mark, 0)

    def test_quote_is_honest_by_default_and_labeled_when_opted_in(self):
        self.assertIsNone(self.market.quote('US.QQQ260914C705000', T))
        mapped = OfflineMarket(FIXTURES, quote_model='option_bar_close')
        quote = mapped.quote('US.QQQ260914C705000', T)
        self.assertIsNotNone(quote)
        self.assertEqual(quote.bid, quote.ask)  # no spread was frozen
        bar = mapped.current_bars('US.QQQ260914C705000', 1, 'K_1M',
                                  boundary=datetime(2026, 9, 14, 10, 0, tzinfo=ET))[0]
        self.assertEqual(quote.as_of, bar.close_time)

    def test_csv_and_parquet_files_agree(self):
        csv_bars = read_bar_file(FIXTURES / 'underlying' / 'US.QQQ.csv', 'US.QQQ')
        parquet = FIXTURES / 'underlying' / 'US.QQQ.parquet'
        if parquet.exists():
            parquet_bars = read_bar_file(parquet, 'US.QQQ')
            self.assertEqual([b.close for b in csv_bars], [b.close for b in parquet_bars])


class FakeFetchMarket:
    """Deterministic in-memory market that still returns real Bar types."""

    def __init__(self, underlying, option):
        self.underlying, self.option = underlying, option

    def history_bars(self, code, ktype, start, end, boundary=None):
        base = self.underlying if code == 'US.QQQ' else self.option
        return [Bar(code, b.close_time, b.open, b.high, b.low, b.close, b.volume, b.interval, 'opend_kline')
                for b in base]

    def current_bars(self, code, count, ktype, boundary=None):
        return self.history_bars(code, ktype, None, None, boundary)[-count:]

    def quote(self, contract, now=None):
        return None

    def underlying_mark(self, symbol, now=None):
        return None


class FetchSliceTests(unittest.TestCase):
    def test_fetch_writes_manifest_cases_csv_parquet_and_zip(self):
        real = OfflineMarket(FIXTURES)
        underlying = day_bars(real, 'US.QQQ', DAY)
        option = day_bars(real, 'US.QQQ260914C705000', DAY)
        market = FakeFetchMarket(underlying, option)
        case = {'symbol': 'US.QQQ', 'direction': 'LONG', 'contract': 'US.QQQ260914C705000', 'trade_date': DAY}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'custody-eval-2026-09-14'
            manifest = fetch_slice(root, trade_date=DAY, cases=[case], market=market,
                                   calendar=OfflineCalendar(), logger=lambda *a, **k: None)
            self.assertTrue((root / 'manifest.json').exists())
            self.assertTrue((root / 'cases.json').exists())
            self.assertTrue((root / 'underlying' / 'US.QQQ.parquet').exists())
            self.assertTrue((root / 'option' / 'US.QQQ260914C705000.csv').exists())
            self.assertTrue((Path(tmp) / 'custody-eval-2026-09-14.zip').exists())
            self.assertEqual(manifest['role'], 'eval/custody')
            self.assertEqual(load_cases(root)['cases'][0]['direction'], 'LONG')
            reloaded = OfflineMarket(root)
            self.assertEqual(len(day_bars(reloaded, 'US.QQQ', DAY)), len(underlying))


class OfflineCalendar:
    def session(self, day):
        from custody.models import Session
        return Session(day, datetime.fromisoformat(day + 'T09:30:00').replace(tzinfo=ET),
                       datetime.fromisoformat(day + 'T16:00:00').replace(tzinfo=ET))


class EvalSessionTests(unittest.TestCase):
    def test_deadline_fallback_is_exactly_one_round_trip_and_no_gates(self):
        market = OfflineMarket(FIXTURES)
        underlying = day_bars(market, 'US.QQQ', DAY)
        option = day_bars(market, 'US.QQQ260914C705000', DAY)
        case = {'symbol': 'US.QQQ', 'direction': 'LONG', 'contract': 'US.QQQ260914C705000', 'trade_date': DAY}
        result = DeadlineFallbackPolicy(entry_at='10:00').run(
            case, underlying, option, datetime(2026, 9, 14, 16, 0, tzinfo=ET))
        self.assertTrue(result['one_round_trip'])
        self.assertGreaterEqual(result['entry']['at'], datetime(2026, 9, 14, 10, 0, tzinfo=ET).isoformat())
        self.assertLess(result['entry']['at'], result['exit']['at'])
        self.assertEqual(result['entry']['basis'], 'option_bar_close')
        self.assertNotIn('entry_ready', json.dumps(result))

    def test_eval_session_reports_all_fixture_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'slice'
            _write_fixture_slice(root)
            report = eval_session(root)
            self.assertEqual(len(report['cases']), 1)
            self.assertTrue(report['cases'][0]['one_round_trip'])
            self.assertEqual(report['role'], 'eval/custody')


def _write_fixture_slice(root):
    """Copy the tiny real fixtures into a self-contained offline slice."""
    root = Path(root)
    (root / 'underlying').mkdir(parents=True, exist_ok=True)
    (root / 'option').mkdir(parents=True, exist_ok=True)
    for rel in ['underlying/US.QQQ.csv', 'underlying/US.QQQ.parquet',
                'option/US.QQQ260914C705000.csv', 'option/US.QQQ260914C705000.parquet']:
        source = FIXTURES / rel
        if source.exists():
            (root / rel).write_bytes(source.read_bytes())
    cases = {'schema_version': 1, 'dataset': 'custody-eval-test',
             'trade_date': DAY,
             'cases': [{'symbol': 'US.QQQ', 'direction': 'LONG',
                        'contract': 'US.QQQ260914C705000', 'trade_date': DAY}]}
    (root / 'cases.json').write_text(json.dumps(cases))
    manifest = {'schema_version': 1, 'dataset': 'custody-eval-test', 'role': 'eval/custody',
                'trade_date': DAY, 'session': {'open': DAY + 'T09:30:00-04:00',
                                               'close': DAY + 'T16:00:00-04:00'},
                'series': [
                    {'code': 'US.QQQ', 'kind': 'underlying', 'csv': 'underlying/US.QQQ.csv',
                     'parquet': 'underlying/US.QQQ.parquet'},
                    {'code': 'US.QQQ260914C705000', 'kind': 'option',
                     'csv': 'option/US.QQQ260914C705000.csv',
                     'parquet': 'option/US.QQQ260914C705000.parquet'}]}
    (root / 'manifest.json').write_text(json.dumps(manifest))


if __name__ == '__main__':
    unittest.main()
