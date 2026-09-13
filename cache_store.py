#!/usr/bin/env python3
"""Local K-line cache store for ``scripts/opend_us_options``.

Design goals
------------
* This module **never** talks to OpenD. It only reads and writes local files, so
  the backtest scaffold and any future scanner can consume cached bars without
  burning ``request_history_kline`` quota.
* Storage is partitioned by ``symbol`` + ``ktype`` under ``cache/klines/``.
  Parquet is preferred when ``pyarrow`` is available; CSV is the fallback so the
  module still works in minimal environments.
* A "fetched ranges" manifest is kept per symbol/ktype so we can prove which
  spans were already requested from OpenD. This is the quota-protection
  guarantee: once a span is recorded, ``missing_ranges()`` will never ask for it
  again — every subsequent read of that range is served from local cache.

Normalized schema (time-ascending, unique on ``time``)::

    time      int64   epoch seconds (derived from OpenD's naive market time)
    open      float64
    high      float64
    low       float64
    close     float64
    volume    float64
    turnover  float64

Time handling note
------------------
OpenD returns ``time_key`` as a naive market-local string (e.g. ``2024-01-02``
for daily bars). We convert it to epoch seconds by treating the naive value as
UTC (``pd.Timestamp(...).value // 10**9``). This is only used for ordering and
range filtering; absolute timezone correctness is not required for the toy
backtests this module supports.
"""

from __future__ import annotations

import json
import numbers
import os
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

__all__ = [
    "KLINE_COLUMNS",
    "TIME_COL",
    "cache_root",
    "get_bars",
    "upsert_bars",
    "coverage",
    "fetched_ranges",
    "record_fetch",
    "missing_ranges",
    "is_fully_cached",
    "list_cached",
]

TIME_COL = "time"
KLINE_COLUMNS = ["time", "open", "high", "low", "close", "volume", "turnover"]

_SCHEMA_DTYPES = {
    "time": "int64",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
    "turnover": "float64",
}

# Environment override so tests / alternate disks can point the cache elsewhere.
_CACHE_DIR_ENV = "OPEND_US_OPTIONS_CACHE_DIR"


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def _module_dir() -> Path:
    return Path(__file__).resolve().parent


def cache_root() -> Path:
    """Root of the local cache. Default: ``<module>/cache``.

    Override with the ``OPEND_US_OPTIONS_CACHE_DIR`` environment variable.
    """
    override = os.environ.get(_CACHE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return _module_dir() / "cache"


def _sanitize(part: str) -> str:
    """Make a symbol / ktype filesystem-safe."""
    return str(part).strip().replace("/", "_").replace("\\", "_")


def _kline_dir(symbol: str) -> Path:
    return cache_root() / "klines" / _sanitize(symbol)


def _bar_path(symbol: str, ktype: str, ext: str) -> Path:
    return _kline_dir(symbol) / f"{_sanitize(ktype)}.{ext}"


# --------------------------------------------------------------------------- #
# serialization helpers
# --------------------------------------------------------------------------- #
def _parquet_available() -> bool:
    try:
        import pyarrow  # noqa: F401

        return True
    except Exception:
        return False


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=d) for c, d in _SCHEMA_DTYPES.items()})


def _to_epoch(series: pd.Series) -> pd.Series:
    """Normalize a time column to nullable int64 epoch seconds.

    Converts datetime values to nanosecond resolution first so the conversion
    is correct regardless of the source unit (pandas 2.x uses ``[ns]``, pandas
    3.x may use ``[us]``).
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        dt = pd.to_datetime(series, errors="coerce").astype("datetime64[ns]")
    elif pd.api.types.is_integer_dtype(series):
        return pd.to_numeric(series, errors="coerce").astype("Int64")
    elif pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").round().astype("Int64")
    else:
        # Could be epoch strings or datetime strings; try numeric first.
        num = pd.to_numeric(series, errors="coerce")
        if num.notna().all() and (num >= 0).all():
            return num.round().astype("Int64")
        dt = pd.to_datetime(series, errors="coerce").astype("datetime64[ns]")

    valid = dt.notna()
    out = pd.Series(pd.NA, index=dt.index, dtype="Int64")
    out.loc[valid] = dt.loc[valid].astype("int64") // 10**9
    return out


def _normalize(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Return a clean, schema-conforming frame (no OpenD access)."""
    if df is None or len(df) == 0:
        return _empty_frame()

    df = df.copy()
    # Accept OpenD's native column name.
    df = df.rename(columns={"time_key": TIME_COL})
    if TIME_COL not in df.columns:
        raise ValueError("bars DataFrame must contain a 'time' or 'time_key' column")

    df[TIME_COL] = _to_epoch(df[TIME_COL])
    df = df.dropna(subset=[TIME_COL])
    df[TIME_COL] = df[TIME_COL].astype("int64")

    for col in KLINE_COLUMNS[1:]:  # skip time
        if col not in df.columns:
            df[col] = float("nan")
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df[KLINE_COLUMNS]
    df = (
        df.drop_duplicates(subset=[TIME_COL])
        .sort_values(TIME_COL)
        .reset_index(drop=True)
    )
    return df


def _coerce_bound(value) -> Optional[int]:
    """Coerce a start/end bound (epoch int, date string, Timestamp) to epoch seconds."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid bound: {value!r}")
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return int(value)
    if isinstance(value, pd.Timestamp):
        return int(value.value // 10**9)
    return int(pd.Timestamp(value).value // 10**9)


def _read_existing(symbol: str, ktype: str) -> pd.DataFrame:
    """Read cached bars from disk only. Never calls OpenD."""
    pq = _bar_path(symbol, ktype, "parquet")
    csv = _bar_path(symbol, ktype, "csv")
    if pq.exists():
        if not _parquet_available():
            raise RuntimeError(
                f"cache has parquet data at {pq} but pyarrow is not installed. "
                "Install pyarrow, or re-warm this symbol/ktype as CSV "
                "(remove the parquet file and re-run the warm-cache CLI)."
            )
        return _normalize(pd.read_parquet(pq))
    if csv.exists():
        return _normalize(pd.read_csv(csv))
    return _empty_frame()


def _write(symbol: str, ktype: str, df: pd.DataFrame) -> None:
    d = _kline_dir(symbol)
    d.mkdir(parents=True, exist_ok=True)
    if _parquet_available():
        target = _bar_path(symbol, ktype, "parquet")
        tmp = target.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, index=False)
        os.replace(tmp, target)
    else:
        target = _bar_path(symbol, ktype, "csv")
        tmp = target.with_suffix(".csv.tmp")
        df.to_csv(tmp, index=False)
        os.replace(tmp, target)


# --------------------------------------------------------------------------- #
# fetched-ranges manifest (quota protection)
# --------------------------------------------------------------------------- #
def _ranges_path(symbol: str, ktype: str) -> Path:
    return _bar_path(symbol, ktype, "fetched.json")


def _merge_spans(spans: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    spans = sorted((int(a), int(b)) for a, b in spans if int(a) <= int(b))
    merged: List[List[int]] = []
    for a, b in spans:
        if not merged or a > merged[-1][1] + 1:
            merged.append([a, b])
        else:
            merged[-1][1] = max(merged[-1][1], b)
    return [(a, b) for a, b in merged]


def _subtract_ranges(start: int, end: int, spans: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Return the parts of ``[start, end]`` not covered by ``spans``."""
    if start > end:
        return []
    merged = _merge_spans(spans)
    out: List[Tuple[int, int]] = []
    cur = start
    for a, b in merged:
        if b < start or a > end:
            continue
        if a > cur:
            out.append((cur, min(a - 1, end)))
        cur = max(cur, b + 1)
        if cur > end:
            break
    if cur <= end:
        out.append((cur, end))
    return out


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def get_bars(
    symbol: str,
    ktype: str = "K_DAY",
    start=None,
    end=None,
) -> pd.DataFrame:
    """Return cached bars for ``symbol``/``ktype`` filtered to ``[start, end]``.

    **Never calls OpenD.** Returns an empty, schema-conforming DataFrame when
    nothing is cached.
    """
    df = _read_existing(symbol, ktype)
    s = _coerce_bound(start)
    e = _coerce_bound(end)
    if s is not None:
        df = df[df[TIME_COL] >= s]
    if e is not None:
        df = df[df[TIME_COL] <= e]
    return df.reset_index(drop=True)


def upsert_bars(symbol: str, ktype: str, bars: pd.DataFrame) -> pd.DataFrame:
    """Merge ``bars`` into the cache (dedupe on time, keep ascending). Returns merged frame."""
    new = _normalize(bars)
    existing = _read_existing(symbol, ktype)
    if existing.empty:
        merged = new
    else:
        merged = _normalize(pd.concat([existing, new], ignore_index=True))
    if not merged.empty:
        _write(symbol, ktype, merged)
    return merged


def coverage(symbol: str, ktype: str = "K_DAY") -> Tuple[Optional[int], Optional[int]]:
    """Return ``(min_ts, max_ts)`` of actually cached bars, or ``(None, None)``."""
    df = _read_existing(symbol, ktype)
    if df.empty:
        return (None, None)
    return (int(df[TIME_COL].min()), int(df[TIME_COL].max()))


def fetched_ranges(symbol: str, ktype: str = "K_DAY") -> List[Tuple[int, int]]:
    """Return the merged list of ``(start_epoch, end_epoch)`` spans already
    requested from OpenD (the quota-protection manifest)."""
    p = _ranges_path(symbol, ktype)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        spans = [(int(a), int(b)) for a, b in data.get("spans", [])]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return []
    return _merge_spans(spans)


def record_fetch(symbol: str, ktype: str, start, end) -> None:
    """Record that ``[start, end]`` was successfully requested from OpenD.

    This is what makes re-reads free: future ``missing_ranges`` calls treat this
    span as covered even if it happened to contain zero bars (e.g. a long
    holiday).
    """
    s = _coerce_bound(start)
    e = _coerce_bound(end)
    if s is None or e is None:
        raise ValueError("record_fetch requires both start and end")
    if s > e:
        return
    spans = fetched_ranges(symbol, ktype)
    spans.append((s, e))
    spans = _merge_spans(spans)

    p = _ranges_path(symbol, ktype)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".fetched.json.tmp")
    tmp.write_text(json.dumps({"spans": [[a, b] for a, b in spans]}, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def missing_ranges(
    symbol: str,
    ktype: str,
    start,
    end,
) -> List[Tuple[int, int]]:
    """Compute the spans of ``[start, end]`` that are NOT yet cached.

    Uses the fetched-ranges manifest first (authoritative for quota protection);
    falls back to actual bar coverage when no manifest exists yet.
    """
    s = _coerce_bound(start)
    e = _coerce_bound(end)
    if s is None or e is None:
        raise ValueError("missing_ranges requires both start and end")
    if s > e:
        return []

    spans = fetched_ranges(symbol, ktype)
    if spans:
        return _subtract_ranges(s, e, spans)

    # No manifest yet: fall back to contiguous (min, max) coverage of bars on disk.
    # NOTE: holes inside [min, max] are invisible without a fetched.json manifest —
    # always warm via fetch_klines.py so spans are recorded.
    mn, mx = coverage(symbol, ktype)
    if mn is None:
        return [(s, e)]
    if mn <= s and mx >= e:
        return []
    return _subtract_ranges(s, e, [(mn, mx)])


def is_fully_cached(symbol: str, ktype: str, start, end) -> bool:
    """True when ``[start, end]`` has no missing ranges (all reads hit cache)."""
    return len(missing_ranges(symbol, ktype, start, end)) == 0


def list_cached() -> List[dict]:
    """Diagnostic listing of everything currently cached."""
    rows = []
    root = cache_root() / "klines"
    if not root.exists():
        return rows
    for sym_dir in sorted(root.iterdir()):
        if not sym_dir.is_dir():
            continue
        symbol = sym_dir.name
        for f in sorted(sym_dir.iterdir()):
            if not (f.name.endswith(".parquet") or f.name.endswith(".csv")):
                continue
            ktype = f.name.rsplit(".", 1)[0]
            df = _read_existing(symbol, ktype)
            mn, mx = coverage(symbol, ktype)
            rows.append(
                {
                    "symbol": symbol,
                    "ktype": ktype,
                    "bars": int(len(df)),
                    "min_ts": mn,
                    "max_ts": mx,
                }
            )
    return rows
