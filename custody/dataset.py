"""The 0DTE custody dataset: layout, verification and loading. Standard library only.

A dataset is a directory (usually an extracted GitHub Release) with this layout::

    <dataset>/
      manifest.json          dataset name, role, window, ...
      cases.json             {"cases": [ ...one entry per option contract... ]}
      CHECKSUMS.sha256       "<sha256>  <relative path>" for every data file
                             (or: manifest.json "series" entries with a per-file "sha256" map)
      underlying/<SYMBOL>.csv    underlying 1m bars, any number of sessions
      option/<CONTRACT>.csv      option 1m bars

CSV columns: ``code,close_time,interval,open,high,low,close,volume`` with an
ISO-8601 bar-close timestamp carrying the ET offset. Parquet twins may exist; this
module only reads CSV.

Case fields used here: ``symbol``, ``contract``, ``trade_date``, ``right`` (or it is
parsed from the code), ``strike``. Optional: ``prev_close`` (previous session close of
the underlying), ``session_close`` (ISO time, for early closes), ``selection``.
``direction`` is always derived from the right (CALL = LONG, PUT = SHORT); any
stored value must agree.

Every case is a true 0DTE contract (expiry == trade_date) with a complete
regular-session underlying tape and at least one traded option bar.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from .marketdata import Bar
from .models import ET, Session, instant

OPTION_CODE = re.compile(r'^US\.([A-Z][A-Z0-9.\-]*?)(\d{6})([CP])(\d{3,})$')
CSV_FIELDS = ('code', 'close_time', 'interval', 'open', 'high', 'low', 'close', 'volume')


class DatasetError(ValueError):
    """The directory is not a valid 0DTE custody dataset."""


# Dataset roles. Training and held-out validation slices never share the same
# role, and a train/custody slice must never contain a validation trade date.
ROLES = ('train/custody', 'validation/custody')


def parse_option_code(code):
    """Return (underlying, expiry ISO date, right, strike) for a Futu US option code."""
    match = OPTION_CODE.match(str(code).strip().upper())
    if not match:
        raise DatasetError('not a US option contract code: %r' % (code,))
    underlying, yymmdd, cp, strike = match.groups()
    expiry = date(2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])).isoformat()
    return underlying, expiry, 'CALL' if cp == 'C' else 'PUT', int(strike) / 1000


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Case:
    symbol: str             # 'US.SPY'
    contract: str           # 'US.SPY260817C776000'
    trade_date: str
    direction: str          # LONG (CALL) / SHORT (PUT)
    strike: float
    prev_close: float | None = None
    session_close: str | None = None
    selection: str | None = None

    @property
    def key(self):
        return self.contract, self.trade_date


@dataclass(frozen=True)
class CaseData:
    case: Case
    session: Session
    underlying: tuple        # completed regular-session 1m Bars, one per minute
    option: tuple            # option 1m Bars within the session with volume > 0


def _bars_from_csv(path, code):
    out = {}
    with Path(path).open(newline='', encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            try:
                bar = Bar(code, instant(row['close_time']), float(row['open']), float(row['high']),
                          float(row['low']), float(row['close']), float(row['volume']),
                          row.get('interval') or '1m', 'frozen')
            except (KeyError, TypeError, ValueError) as exc:
                raise DatasetError('bad bar row in %s: %s' % (path, exc)) from exc
            if bar.interval != '1m':
                raise DatasetError('non-1m bar in ' + str(path))
            out[bar.close_time] = bar
    return [out[k] for k in sorted(out)]


def default_session(day, close=None):
    d = date.fromisoformat(day)
    closes = instant(close) if close else datetime.combine(d, time(16), tzinfo=ET)
    return Session(day, datetime.combine(d, time(9, 30), tzinfo=ET), closes)


class Dataset:
    """Verified read access to one dataset directory."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        try:
            self.manifest = json.loads((self.root / 'manifest.json').read_text(encoding='utf-8'))
            raw_cases = json.loads((self.root / 'cases.json').read_text(encoding='utf-8'))['cases']
        except (OSError, KeyError, ValueError) as exc:
            raise DatasetError('missing or unreadable manifest.json/cases.json under %s' % self.root) from exc
        self.name = self.manifest.get('dataset') or self.root.name
        self._pinned = set()
        self.checksums_verified = self.verify_checksums()
        checksums = self.root / 'CHECKSUMS.sha256'
        pin_file = checksums if checksums.exists() else self.root / 'manifest.json'
        self.fingerprint = sha256_file(pin_file)
        self.pinned_by = pin_file.name
        self.cases = [self._case(item) for item in raw_cases]
        keys = [c.key for c in self.cases]
        if len(set(keys)) != len(keys):
            raise DatasetError('duplicate (contract, trade_date) case')
        self._tapes = {}

    # ------------------------------------------------------------ verification
    def verify_checksums(self):
        """Verify every pinned data file; remember which files are pinned.

        Pins come from ``CHECKSUMS.sha256`` or, when that file is absent, from the
        per-file ``sha256`` maps of ``manifest.json`` ``series`` entries (the layout of
        the custody validation Releases). A case may only read pinned files.
        """
        path = self.root / 'CHECKSUMS.sha256'
        pins = []
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    digest, name = line.split(maxsplit=1)
                    pins.append((name.strip().lstrip('*'), digest))
        else:
            for item in self.manifest.get('series') or []:
                for fmt, digest in (item.get('sha256') or {}).items():
                    name = item.get(fmt, fmt)
                    pins.append((name, digest))
            if not pins:
                raise DatasetError('no checksums: add CHECKSUMS.sha256 or manifest series sha256 for every data file')
        for name, digest in pins:
            target = (self.root / name).resolve()
            if not target.is_relative_to(self.root) or not target.is_file():
                raise DatasetError('checksum entry points outside the dataset or is missing: ' + name)
            if sha256_file(target) != digest:
                raise DatasetError('checksum mismatch: ' + name)
            self._pinned.add(target)
        if not pins:
            raise DatasetError('CHECKSUMS.sha256 lists no files')
        return len(pins)

    def _case(self, item):
        try:
            contract = str(item['contract']).upper()
            symbol = str(item['symbol']).upper()
            trade_date = date.fromisoformat(item['trade_date']).isoformat()
        except (KeyError, TypeError, ValueError) as exc:
            raise DatasetError('case needs symbol, contract and trade_date: %r' % (item,)) from exc
        underlying, expiry, right, strike = parse_option_code(contract)
        if 'US.' + underlying != symbol:
            raise DatasetError('contract %s does not belong to %s' % (contract, symbol))
        if expiry != trade_date:
            raise DatasetError('%s is not 0DTE on %s (expiry %s)' % (contract, trade_date, expiry))
        direction = 'LONG' if right == 'CALL' else 'SHORT'
        if item.get('right', right) != right or item.get('direction', direction) != direction:
            raise DatasetError('stored right/direction disagrees with contract ' + contract)
        prev_close = item.get('prev_close')
        return Case(symbol, contract, trade_date, direction, strike,
                    float(prev_close) if prev_close is not None else None,
                    item.get('session_close'), item.get('selection'))

    # ------------------------------------------------------------ loading
    def _tape(self, kind, code):
        key = (kind, code)
        if key not in self._tapes:
            path = self.root / kind / (code + '.csv')
            if not path.is_file():
                raise DatasetError('missing %s series %s' % (kind, code))
            if path.resolve() not in self._pinned:
                raise DatasetError('data file is not pinned by a checksum: %s/%s.csv' % (kind, code))
            self._tapes[key] = _bars_from_csv(path, code)
        return self._tapes[key]

    def load(self, case: Case) -> CaseData:
        session = default_session(case.trade_date, case.session_close)
        underlying = [b for b in self._tape('underlying', case.symbol) if session.opens < b.close_time <= session.closes]
        expected = int((session.closes - session.opens).total_seconds() // 60)
        minutes = [int((b.close_time - session.opens).total_seconds() // 60) for b in underlying]
        if minutes != list(range(1, expected + 1)):
            raise DatasetError('%s %s: underlying tape is not a complete 1m session (%d/%d bars)'
                               % (case.symbol, case.trade_date, len(underlying), expected))
        option = [b for b in self._tape('option', case.contract)
                  if session.opens < b.close_time <= session.closes and b.volume > 0 and b.close > 0]
        if not option:
            raise DatasetError('%s has no traded option bars in the session' % case.contract)
        return CaseData(case, session, tuple(underlying), tuple(option))

    def sessions(self):
        return sorted({c.trade_date for c in self.cases})

    def sides_report(self):
        """Both-sides requirement: every (symbol, trade_date) needs a CALL and a PUT on the same strike."""
        groups = {}
        for case in self.cases:
            groups.setdefault((case.symbol, case.trade_date), []).append(case)
        missing = []
        for (symbol, day), cases in sorted(groups.items()):
            calls = {c.strike for c in cases if c.direction == 'LONG'}
            puts = {c.strike for c in cases if c.direction == 'SHORT'}
            if not calls & puts:
                missing.append({'symbol': symbol, 'trade_date': day, 'call_strikes': sorted(calls),
                                'put_strikes': sorted(puts)})
        return {'symbol_sessions': len(groups), 'both_sides': len(groups) - len(missing),
                'ok': bool(groups) and not missing, 'missing': missing}


# ---------------------------------------------------------------- writing
def write_bars(path, bars):
    """Merge ``bars`` into a CSV series (dedupe by close_time, sorted)."""
    path = Path(path)
    merged = {}
    if path.exists():
        for bar in _bars_from_csv(path, bars[0].code if bars else path.stem):
            merged[bar.close_time] = bar
    for bar in bars:
        merged[bar.close_time] = bar
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.csv.tmp')
    with tmp.open('w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_FIELDS)
        for key in sorted(merged):
            b = merged[key]
            writer.writerow([b.code, b.close_time.isoformat(), b.interval, b.open, b.high, b.low, b.close, b.volume])
    tmp.replace(path)


def write_checksums(root):
    root = Path(root)
    lines = []
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.name != 'CHECKSUMS.sha256' and not path.name.endswith('.tmp'):
            lines.append('%s  %s' % (sha256_file(path), path.relative_to(root).as_posix()))
    (root / 'CHECKSUMS.sha256').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return len(lines)


# ---------------------------------------------------------------- release check
def isolation_report(reference, validation):
    """Prove a train slice and its held-out validation slice are disjoint.

    Fails when the two roles are not exactly one ``train/custody`` and one
    ``validation/custody``, when any trade date is shared, or when any
    ``(symbol, trade_date)`` session is shared. Validation must never be used
    for fitting, so a leak is a release blocker (docs/DATA.md).
    """
    try:
        train, held = Dataset(reference), Dataset(validation)
    except DatasetError as exc:
        return {'ok': False, 'errors': [str(exc)], 'reference': str(reference), 'validation': str(validation)}
    errors = []
    train_role, held_role = train.manifest.get('role'), held.manifest.get('role')
    if {train_role, held_role} != set(ROLES):
        errors.append('expected roles %s, got %r and %r' % (list(ROLES), train_role, held_role))
    train_dates, held_dates = set(train.sessions()), set(held.sessions())
    train_sessions = {(c.symbol, c.trade_date) for c in train.cases}
    held_sessions = {(c.symbol, c.trade_date) for c in held.cases}
    date_overlap = sorted(train_dates & held_dates)
    session_overlap = sorted(train_sessions & held_sessions)
    if date_overlap:
        errors.append('train and validation share trade dates: ' + ', '.join(date_overlap))
    if session_overlap:
        errors.append('train and validation share (symbol, trade_date) sessions: '
                      + ', '.join('%s|%s' % key for key in session_overlap))
    return {'ok': not errors, 'errors': errors, 'reference': train.name, 'validation': held.name,
            'reference_role': train_role, 'validation_role': held_role,
            'reference_dates': sorted(train_dates), 'validation_dates': sorted(held_dates),
            'date_overlap': date_overlap, 'session_overlap': session_overlap}


def check(root, validation=None):
    """Everything a Release must satisfy before publication (docs/DATA.md).

    With ``validation`` given it additionally proves the held-out slice is
    disjoint (roles, trade dates and sessions), so a release cannot publish a
    leak.
    """
    try:
        dataset = Dataset(root)
    except DatasetError as exc:
        return {'root': str(root), 'ok': False, 'error': str(exc)}
    failures = []
    for case in dataset.cases:
        try:
            dataset.load(case)
        except DatasetError as exc:
            failures.append({'contract': case.contract, 'trade_date': case.trade_date, 'error': str(exc)})
    sides = dataset.sides_report()
    report = {'dataset': dataset.name, 'pinned_by': dataset.pinned_by, 'checksums_verified': dataset.checksums_verified,
              'cases': len(dataset.cases), 'sessions': dataset.sessions(), 'load_failures': failures,
              'sides': sides, 'ok': not failures and sides['ok']}
    if validation:
        report['isolation'] = isolation_report(root, validation)
        report['ok'] = report['ok'] and report['isolation']['ok']
    return report


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog='custody check', description=check.__doc__)
    parser.add_argument('--dataset', required=True, help='extracted dataset directory')
    parser.add_argument('--validation', default=None,
                        help='held-out dataset; fail on a role mismatch, shared trade date or shared session')
    args = parser.parse_args(argv)
    result = check(args.dataset, args.validation)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result['ok'] else 1
