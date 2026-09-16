"""The 0DTE custody dataset: layout, verification and loading. Standard library only.

A dataset is a directory (usually an extracted GitHub Release) with this layout::

    <dataset>/
      manifest.json          dataset name, role, window, ...
      cases.json             {"cases": [ ...one entry per option contract... ]}
      CHECKSUMS.sha256       "<sha256>  <relative path>" for every data file
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
    with Path(path).open(newline='') as fh:
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

    def __init__(self, root, verify=True):
        self.root = Path(root).resolve()
        try:
            self.manifest = json.loads((self.root / 'manifest.json').read_text())
            raw_cases = json.loads((self.root / 'cases.json').read_text())['cases']
        except (OSError, KeyError, ValueError) as exc:
            raise DatasetError('missing or unreadable manifest.json/cases.json under %s' % self.root) from exc
        self.name = self.manifest.get('dataset') or self.root.name
        self.checksums_verified = self.verify_checksums() if verify else 0
        checksums = self.root / 'CHECKSUMS.sha256'
        self.fingerprint = sha256_file(checksums) if checksums.exists() else None
        self.cases = [self._case(item) for item in raw_cases]
        keys = [c.key for c in self.cases]
        if len(set(keys)) != len(keys):
            raise DatasetError('duplicate (contract, trade_date) case')
        self._tapes = {}

    # ------------------------------------------------------------ verification
    def verify_checksums(self):
        path = self.root / 'CHECKSUMS.sha256'
        if not path.exists():
            raise DatasetError('CHECKSUMS.sha256 missing: a dataset must pin every data file')
        checked = 0
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            digest, name = line.split(maxsplit=1)
            target = (self.root / name.strip().lstrip('*')).resolve()
            if not target.is_relative_to(self.root) or not target.is_file():
                raise DatasetError('checksum entry points outside the dataset or is missing: ' + name)
            if sha256_file(target) != digest:
                raise DatasetError('checksum mismatch: ' + name)
            checked += 1
        if not checked:
            raise DatasetError('CHECKSUMS.sha256 lists no files')
        return checked

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
    with tmp.open('w', newline='') as fh:
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
    (root / 'CHECKSUMS.sha256').write_text('\n'.join(lines) + '\n')
    return len(lines)
