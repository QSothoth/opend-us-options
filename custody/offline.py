"""Frozen-slice market-data provider implementing the shared boundary.

``OfflineMarket`` reads the custody eval slice produced by
:mod:`custody.eval_slice` (``manifest.json`` + per-code CSV/parquet 1-minute
bars) and exposes exactly the same :class:`~custody.marketdata.MarketDataProvider`
surface as the live read-only :class:`~custody.opend.OpenDMarket`. Callers can
therefore swap providers without rewriting the controller or eval harness.

The frozen slice holds **trade OHLCV** for the underlying and the option. It
does not hold NBBO, so ``quote`` is honest by default: it returns ``None``.
Pass ``quote_model='option_bar_close'`` only for the clearly-labelled
must-trade plumbing stub, where the option bar close is used as the reference
fill price (bid == ask) because no spread was frozen. Live never does this.
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from .marketdata import Bar, MarketDataProvider, _et
from .models import ET, Quote, instant

MANIFEST_NAME = 'manifest.json'
CASES_NAME = 'cases.json'
log = logging.getLogger('custody.offline')


def load_manifest(root):
    path = Path(root) / MANIFEST_NAME
    if not path.exists():
        return {'schema_version': 1, 'series': [], 'root': str(Path(root))}
    return json.loads(path.read_text())


def load_cases(root):
    path = Path(root) / CASES_NAME
    if not path.exists():
        raise FileNotFoundError('custody eval cases.json not found under ' + str(root))
    return json.loads(path.read_text())


def _rows_from_csv(path):
    with Path(path).open(newline='') as fh:
        return list(csv.DictReader(fh))


def _rows_from_parquet(path):
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - pandas is present in the eval venv
        raise RuntimeError('pandas/pyarrow required to read parquet frozen bars') from exc
    frame = pd.read_parquet(path)
    if 'close_time' in frame.columns:
        frame = frame.copy()
        frame['close_time'] = frame['close_time'].map(
            lambda value: instant(value).isoformat() if not isinstance(value, str) else value)
    return frame.to_dict(orient='records')


def _bar_from_row(row, default_code=None):
    code = (row.get('code') or default_code or '').strip().upper()
    when = _et(row.get('close_time'))
    if when is None:
        return None
    try:
        values = {key: float(row[key]) for key in ('open', 'high', 'low', 'close', 'volume')}
    except (KeyError, TypeError, ValueError):
        return None
    try:
        return Bar(code, when, values['open'], values['high'], values['low'], values['close'],
                   values['volume'], row.get('interval') or '1m', 'frozen')
    except ValueError:
        return None


def read_bar_file(path, code=None):
    """Read a frozen CSV/parquet file into ordered, deduped :class:`Bar`s."""
    path = Path(path)
    rows = _rows_from_parquet(path) if path.suffix == '.parquet' else _rows_from_csv(path)
    out = []
    for row in rows:
        bar = _bar_from_row(row, default_code=code)
        if bar is not None:
            out.append(bar)
    dedup = {bar.close_time: bar for bar in out}
    return [dedup[key] for key in sorted(dedup)]


def _bound(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return instant(value).astimezone(ET)
    parsed = _et(value)
    return parsed if parsed is not None else fallback


class OfflineMarket:
    """Read-only provider over a frozen custody eval slice directory."""

    def __init__(self, root, quote_model='none'):
        self.root = Path(root)
        self.quote_model = quote_model
        self.manifest = load_manifest(self.root)
        self._files = {}
        series = self.manifest.get('series') or []
        for item in series:
            code = str(item.get('code', '')).strip().upper()
            if not code:
                continue
            candidate = item.get('parquet') or item.get('csv')
            if candidate:
                self._files[code] = (self.root / candidate)
        if not self._files:
            self._scan()
        self._cache = {}

    def _scan(self):
        for kind in ('underlying', 'option'):
            folder = self.root / kind
            if not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                if path.suffix not in ('.csv', '.parquet'):
                    continue
                self._files.setdefault(path.stem.upper(), path)

    def codes(self):
        return sorted(self._files)

    def _bars(self, code):
        code = str(code).strip().upper()
        if code not in self._files:
            raise KeyError('code not present in frozen custody slice: ' + code)
        if code not in self._cache:
            self._cache[code] = read_bar_file(self._files[code], code=code)
        return self._cache[code]

    # --- Shared MarketDataProvider surface ---------------------------------
    def history_bars(self, code, ktype, start, end, boundary=None):
        if str(ktype).upper() not in ('K_1M', 'K1M'):
            log.warning('offline custody slice holds 1m bars only; %s unavailable for %s', ktype, code)
            return []
        bars = self._bars(code)
        lo = _bound(start, datetime.min.replace(tzinfo=ET))
        hi = _bound(end, datetime.max.replace(tzinfo=ET))
        limit = _bound(boundary, None)
        return [bar for bar in bars if lo <= bar.close_time <= hi and (limit is None or bar.close_time <= limit)]

    def current_bars(self, code, count, ktype, boundary=None):
        bars = self.history_bars(code, ktype, None, None, boundary=boundary)
        count = int(count)
        return bars[-count:] if count > 0 else []

    def underlying_mark(self, symbol, now=None):
        code = str(symbol).strip().upper()
        if not code.startswith('US.'):
            code = 'US.' + code
        if code not in self._files:
            return None
        bars = self._bars(code)
        limit = _bound(now, None)
        eligible = [bar for bar in bars if limit is None or bar.close_time <= limit]
        return eligible[-1].close if eligible else None

    def quote(self, contract, now=None):
        if self.quote_model != 'option_bar_close':
            return None
        code = str(contract).strip().upper()
        if code not in self._files:
            return None
        bars = self._bars(code)
        limit = _bound(now, None)
        eligible = [bar for bar in bars if bar.close_time <= limit] if limit is not None else bars
        if not eligible:
            return None
        bar = eligible[-1]
        # Plumbing stub only: frozen trade close stands in for both sides of the
        # spread because no NBBO was frozen. Live uses real bid/ask quotes.
        return Quote(code, bar.close, bar.close, bar.close_time)
