import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from helpers import DAY, option_bars, path_bars, piecewise, write_dataset  # noqa: E402

from custody.dataset import Dataset, DatasetError, parse_option_code, write_bars, write_checksums  # noqa: E402

CALL, PUT = 'US.SPY260914C100000', 'US.SPY260914P100000'


def spy_cases(**overrides):
    closes = piecewise([(1, 100.0), (390, 101.0)])
    base = [{'symbol': 'US.SPY', 'contract': CALL, 'trade_date': DAY, 'underlying': closes,
             'option': option_bars(closes, 100, 'CALL', CALL)},
            {'symbol': 'US.SPY', 'contract': PUT, 'trade_date': DAY, 'underlying': closes,
             'option': option_bars(closes, 100, 'PUT', PUT)}]
    base[0].update(overrides)
    return base


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'ds'

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_option_code(self):
        self.assertEqual(parse_option_code('US.SPY260914C776500'), ('SPY', '2026-09-14', 'CALL', 776.5))
        self.assertEqual(parse_option_code('us.brk.b260918p400000'), ('BRK.B', '2026-09-18', 'PUT', 400.0))
        with self.assertRaises(DatasetError):
            parse_option_code('SPY260914C776500')

    def test_valid_dataset_loads_cases_and_complete_tapes(self):
        write_dataset(self.root, spy_cases(prev_close=99.5, selection='both_sides_atm_at_open'))
        ds = Dataset(self.root)
        self.assertEqual(ds.name, 'test-dataset')
        self.assertGreater(ds.checksums_verified, 0)
        self.assertEqual([c.direction for c in ds.cases], ['LONG', 'SHORT'])
        self.assertEqual((ds.cases[0].prev_close, ds.cases[0].selection), (99.5, 'both_sides_atm_at_open'))
        data = ds.load(ds.cases[0])
        self.assertEqual(len(data.underlying), 390)
        self.assertTrue(all(b.volume > 0 for b in data.option))
        self.assertEqual(ds.sessions(), [DAY])

    def test_checksums_are_mandatory_and_enforced(self):
        write_dataset(self.root, spy_cases())
        with (self.root / 'option' / (CALL + '.csv')).open('a', encoding='utf-8') as fh:
            fh.write('US.SPY260914C100000,2026-09-14T15:59:00-04:00,1m,1,1,1,1,1\n')
        with self.assertRaisesRegex(DatasetError, 'checksum mismatch'):
            Dataset(self.root)
        (self.root / 'CHECKSUMS.sha256').unlink()
        with self.assertRaisesRegex(DatasetError, 'CHECKSUMS'):
            Dataset(self.root)

    def test_only_true_0dte_matching_contracts_are_cases(self):
        for bad, message in ((dict(contract='US.SPY260918C100000'), 'not 0DTE'),
                             (dict(contract='US.QQQ260914C100000'), 'does not belong'),
                             (dict(direction='SHORT'), 'disagrees')):
            with self.subTest(message):
                root = Path(self.tmp.name) / message.replace(' ', '_')
                cases = spy_cases(**bad)
                write_dataset(root, cases)
                with self.assertRaisesRegex(DatasetError, message):
                    Dataset(root)

    def test_incomplete_underlying_or_untraded_option_is_refused(self):
        write_dataset(self.root, spy_cases())
        tape = self.root / 'underlying' / 'US.SPY.csv'
        lines = tape.read_text(encoding='utf-8').splitlines()
        tape.write_text('\n'.join(lines[:100] + lines[101:]) + '\n', encoding='utf-8')
        write_checksums(self.root)
        ds = Dataset(self.root)
        with self.assertRaisesRegex(DatasetError, 'not a complete 1m session'):
            ds.load(ds.cases[0])
        root2 = Path(self.tmp.name) / 'untraded'
        cases = spy_cases()
        cases[0]['option'] = [b.__class__(**{**b.__dict__, 'volume': 0.0}) for b in cases[0]['option']]
        write_dataset(root2, cases)
        ds2 = Dataset(root2)
        with self.assertRaisesRegex(DatasetError, 'no traded option bars'):
            ds2.load(ds2.cases[0])

    def test_manifest_series_checksums_pin_files_like_the_validation_releases(self):
        import hashlib
        write_dataset(self.root, spy_cases())
        (self.root / 'CHECKSUMS.sha256').unlink()
        series = []
        for kind, code in (('underlying', 'US.SPY'), ('option', CALL), ('option', PUT)):
            rel = '%s/%s.csv' % (kind, code)
            series.append({'code': code, 'kind': kind, 'csv': rel,
                           'sha256': {'csv': hashlib.sha256((self.root / rel).read_bytes()).hexdigest()}})
        manifest = {'dataset': 'manifest-pinned', 'series': series}
        (self.root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        ds = Dataset(self.root)
        self.assertEqual((ds.checksums_verified, ds.pinned_by), (3, 'manifest.json'))
        self.assertEqual(len(ds.load(ds.cases[1]).underlying), 390)
        (self.root / 'manifest.json').write_text(json.dumps(dict(manifest, series=series[:2])), encoding='utf-8')
        ds = Dataset(self.root)
        with self.assertRaisesRegex(DatasetError, 'not pinned'):
            ds.load(ds.cases[1])
        series[0]['sha256']['csv'] = '0' * 64
        (self.root / 'manifest.json').write_text(json.dumps(dict(manifest, series=series)), encoding='utf-8')
        with self.assertRaisesRegex(DatasetError, 'checksum mismatch'):
            Dataset(self.root)

    def test_both_sides_requirement_and_release_check(self):
        from custody.dataset import check
        import contextlib, io
        from custody.__main__ import main
        write_dataset(self.root, spy_cases())
        self.assertEqual(Dataset(self.root).sides_report(), {'symbol_sessions': 1, 'both_sides': 1, 'ok': True, 'missing': []})
        self.assertTrue(check(self.root)['ok'])
        one_sided = Path(self.tmp.name) / 'one_sided'
        write_dataset(one_sided, spy_cases()[:1])
        report = Dataset(one_sided).sides_report()
        self.assertEqual((report['ok'], report['missing'][0]['put_strikes']), (False, []))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['check', '--dataset', str(one_sided)]), 1)
            self.assertEqual(main(['check', '--dataset', str(self.root)]), 0)
        mismatched = Path(self.tmp.name) / 'mismatched'
        cases = spy_cases()
        other_put = 'US.SPY260914P101000'
        cases[1].update(contract=other_put, option=option_bars(cases[1]['underlying'], 101, 'PUT', other_put))
        write_dataset(mismatched, cases)
        self.assertFalse(Dataset(mismatched).sides_report()['ok'])  # CALL and PUT must share the strike
        broken = Path(self.tmp.name) / 'broken'
        write_dataset(broken, spy_cases())
        (broken / 'CHECKSUMS.sha256').write_text('0' * 64 + '  cases.json\n', encoding='utf-8')
        self.assertFalse(check(broken)['ok'])

    def test_isolation_report_blocks_role_and_date_leak(self):
        from custody.dataset import isolation_report

        def relabel(root, role, name):
            manifest = json.loads((root / 'manifest.json').read_text())
            manifest.update(role=role, dataset=name)
            (root / 'manifest.json').write_text(json.dumps(manifest))
            write_checksums(root)

        def shifted(day):
            cases = spy_cases()
            codes = ('US.SPY260915C100000', 'US.SPY260915P100000')
            for case, code in zip(cases, codes):
                case.update(contract=code, trade_date=day,
                            option=option_bars(case['underlying'], 100, 'CALL' if case is cases[0] else 'PUT',
                                               code, day=day))
            return cases

        train = Path(self.tmp.name) / 'train'
        write_dataset(train, spy_cases())
        relabel(train, 'train/custody', 'train')
        held = Path(self.tmp.name) / 'held'
        write_dataset(held, shifted('2026-09-15'))
        relabel(held, 'validation/custody', 'held')
        report = isolation_report(train, held)
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['date_overlap'], [])

        # same trade date -> leak; also a copy that keeps the train role is rejected.
        leak = Path(self.tmp.name) / 'leak'
        write_dataset(leak, spy_cases())
        relabel(leak, 'validation/custody', 'leak')
        bad = isolation_report(train, leak)
        self.assertFalse(bad['ok'])
        self.assertEqual(bad['date_overlap'], [DAY])
        self.assertTrue(any('share trade dates' in e for e in bad['errors']))
        wrong_role = Path(self.tmp.name) / 'wrong_role'
        write_dataset(wrong_role, shifted('2026-09-15'))
        relabel(wrong_role, 'train/custody', 'wrong_role')
        self.assertFalse(isolation_report(train, wrong_role)['ok'])

    def test_duplicate_cases_are_refused(self):
        cases = spy_cases()
        write_dataset(self.root, [cases[0], dict(cases[0])])
        with self.assertRaisesRegex(DatasetError, 'duplicate'):
            Dataset(self.root)

    def test_write_bars_merges_and_dedupes(self):
        bars = path_bars([100.0, 101.0, 102.0])
        target = self.root / 'underlying' / 'US.SPY.csv'
        write_bars(target, bars[:2])
        write_bars(target, bars[1:])
        rows = target.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(rows), 4)  # header + 3 unique minutes
        self.assertTrue(rows[0].startswith('code,close_time'))

    def test_release_layout_extra_fields_are_tolerated(self):
        write_dataset(self.root, spy_cases())
        doc = json.loads((self.root / 'cases.json').read_text(encoding='utf-8'))
        doc['cases'][0].update(call_volume=1.0, chosen_volume=2.0, expiry=DAY, right='CALL', strike=100.0)
        (self.root / 'cases.json').write_text(json.dumps(doc), encoding='utf-8')
        write_checksums(self.root)
        self.assertEqual(len(Dataset(self.root).cases), 2)


if __name__ == '__main__':
    unittest.main()
