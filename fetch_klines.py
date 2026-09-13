#!/usr/bin/env python3
"""Warm-cache CLI for underlying K-lines (read-only OpenD).

Only fetches the ranges that are **missing** from the local cache. Anything
already cached is never requested again from OpenD, protecting
``request_history_kline`` quota.

Defaults
--------
* Dry-run: computes and prints the fetch plan, but makes **no** OpenD calls.
  Pass ``--execute`` to actually fetch (``--no-fetch`` forces dry-run).
* Allowlist: US.SPY, US.QQQ, US.AAPL, US.NVDA, US.TSLA, US.MU, US.SNDK, US.SKHY.
  Override with ``--symbols`` or the ``OPEND_US_OPTIONS_ALLOWLIST`` env var
  (comma-separated).

Read-only guarantee: this module uses ``OpenQuoteContext`` only. It never
imports or uses ``unlock_trade`` / ``place_order`` / ``OpenSecTradeContext``.

Examples
--------
    # Dry-run: show planned fetches, no OpenD call
    python fetch_klines.py --start 2023-01-01 --end 2024-12-31

    # Actually warm the cache
    python fetch_klines.py --start 2023-01-01 --end 2024-12-31 --execute

    # Override allowlist + ktype
    python fetch_klines.py --symbols US.SPY US.QQQ --ktype K_DAY \
        --start 2023-01-01 --end 2024-12-31 --execute
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import cache_store
from rate_limit import history_kline_limiter

DEFAULT_ALLOWLIST = [
    "US.SPY",
    "US.QQQ",
    "US.AAPL",
    "US.NVDA",
    "US.TSLA",
    "US.MU",
    "US.SNDK",
    "US.SKHY",
]
ALLOWLIST_ENV = "OPEND_US_OPTIONS_ALLOWLIST"

DEFAULT_KTYPE = "K_DAY"
MAX_BARS_PER_REQUEST = 800  # per-call cap is 1000; stay safely under

# request_history_kline official limit is 60/30s -> use 50/30s.
DEFAULT_MAX_CALLS = 50
DEFAULT_WINDOW_SECONDS = 30.0

# Ktype metadata for formatting start/end and advancing the cursor.
KTYPE_META = {
    "K_DAY": {"step": "1D", "fmt": "%Y-%m-%d"},
    "K_WEEK": {"step": "7D", "fmt": "%Y-%m-%d"},
    "K_MON": {"step": "1MS", "fmt": "%Y-%m"},
    "K_1M": {"step": "1min", "fmt": "%Y-%m-%d %H:%M:%S"},
    "K_5M": {"step": "5min", "fmt": "%Y-%m-%d %H:%M:%S"},
    "K_15M": {"step": "15min", "fmt": "%Y-%m-%d %H:%M:%S"},
    "K_30M": {"step": "30min", "fmt": "%Y-%m-%d %H:%M:%S"},
    "K_60M": {"step": "60min", "fmt": "%Y-%m-%d %H:%M:%S"},
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _parse(ts) -> pd.Timestamp:
    return ts if isinstance(ts, pd.Timestamp) else pd.Timestamp(ts)


def _fmt(ts: pd.Timestamp, ktype: str) -> str:
    return ts.strftime(KTYPE_META[ktype]["fmt"])


def _advance(ts: pd.Timestamp, ktype: str) -> pd.Timestamp:
    step = KTYPE_META[ktype]["step"]
    if step == "1MS":
        return (ts + pd.DateOffset(months=1)).normalize()
    return ts + pd.Timedelta(step)


def _epoch_to_ts(epoch: int) -> pd.Timestamp:
    return pd.to_datetime(epoch, unit="s")


def _resolve_allowlist(symbols) -> List[str]:
    if symbols:
        raw = [s.strip().upper() for s in symbols if s.strip()]
    else:
        env = os.environ.get(ALLOWLIST_ENV)
        raw = [s.strip().upper() for s in (env.split(",") if env else DEFAULT_ALLOWLIST) if s.strip()]
    seen, out = set(), []
    for s in raw:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _quote_ctx():
    """Create a read-only OpenQuoteContext. Imported lazily so dry-run needs no futu-api."""
    try:
        from futu import OpenQuoteContext
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise SystemExit(
            "futu-api is not installed. Install it (pip install futu-api) to use --execute. "
            "Dry-run does not need futu-api."
        ) from exc
    host = os.environ.get("FUTU_HOST", "127.0.0.1")
    port = int(os.environ.get("FUTU_PORT", "11111"))
    return OpenQuoteContext(host=host, port=port)


def _to_kltype(ktype: str):
    try:
        from futu import KLType
    except ImportError:  # pragma: no cover
        return None
    kl = getattr(KLType, ktype, None)
    if kl is None:
        raise SystemExit(f"unknown ktype {ktype!r}; known: {sorted(KTYPE_META)}")
    return kl


def _to_autype(autype: str):
    try:
        from futu import AuType
    except ImportError:  # pragma: no cover
        return None
    au = getattr(AuType, autype.upper(), None)
    if au is None:
        raise SystemExit(f"unknown autype {autype!r}; use one of QFQ/HFQ/NONE")
    return au


def _print_quota(ctx, label: str) -> None:
    """Best-effort print of the history-kline quota. Never fatal."""
    try:
        ret, data = ctx.get_history_kl_quota(get_detail=False)
        if ret == 0:
            if hasattr(data, "to_dict"):
                data = data.to_dict(orient="records")
            print(f"  [quota {label}] {data}")
        else:
            print(f"  [quota {label}] unavailable (ret={ret})")
    except Exception as exc:  # noqa: BLE001
        print(f"  [quota {label}] unavailable: {exc}")


def fetch_span(ctx, symbol: str, ktype: str, start_epoch: int, end_epoch: int, limiter) -> int:
    """Fetch ``[start_epoch, end_epoch]`` from OpenD via ``page_req_key`` paging.

    Records every successfully requested span in the cache manifest (even when
    it returned zero bars) so it is never re-requested. Returns the number of
    bar rows written to cache.

    futu-api returns ``(ret, data, page_req_key)``. Keep the same start/end and
    pass ``page_req_key`` until it comes back ``None``.
    """
    kltype = _to_kltype(ktype)
    start_str = _fmt(_epoch_to_ts(start_epoch), ktype)
    end_str = _fmt(_epoch_to_ts(end_epoch), ktype)
    frames: List[pd.DataFrame] = []
    page_req_key = None

    while True:
        limiter.acquire()
        ret, data, page_req_key = ctx.request_history_kline(
            symbol,
            start=start_str,
            end=end_str,
            ktype=kltype,
            autype=_to_autype("QFQ"),
            max_count=MAX_BARS_PER_REQUEST,
            page_req_key=page_req_key,
        )
        if ret != 0:
            raise RuntimeError(
                f"request_history_kline failed for {symbol} {ktype}: ret={ret} data={data}"
            )

        if data is not None and not getattr(data, "empty", True):
            frames.append(data)

        if page_req_key is None:
            break
        time.sleep(0.05)

    if not frames:
        # No bars in this span (e.g. long holiday). Record it so we never
        # waste quota re-requesting the same empty range.
        cache_store.record_fetch(symbol, ktype, start_str, end_str)
        return 0

    df = pd.concat(frames, ignore_index=True)
    # Record the full requested span (not just last bar) so tiny post-session
    # gaps never trigger a re-fetch / quota burn.
    cache_store.record_fetch(symbol, ktype, start_str, end_str)
    merged = cache_store.upsert_bars(symbol, ktype, df)
    time.sleep(0.05)
    return int(len(merged))




def _build_plan(allowlist: List[str], ktype: str, start: str, end: str) -> Dict[str, dict]:
    """Compute missing ranges for every symbol BEFORE any OpenD call."""
    plan: Dict[str, dict] = {}
    for sym in allowlist:
        cov = cache_store.coverage(sym, ktype)
        missing = cache_store.missing_ranges(sym, ktype, start, end)
        plan[sym] = {"coverage": cov, "missing": missing, "cache_hit": len(missing) == 0}
    return plan


def _print_plan(plan: Dict[str, dict], ktype: str, execute: bool) -> int:
    mode = "EXECUTE" if execute else "DRY-RUN"
    print(f"== {mode}: allowlist warm-cache plan (ktype={ktype}) ==")
    total_fetches = 0
    for sym, info in plan.items():
        missing = info["missing"]
        if missing:
            total_fetches += len(missing)
            print(f"{sym}: FETCH {len(missing)} missing span(s)")
            for s, e in missing:
                print(f"    {_fmt(_epoch_to_ts(s), ktype)} .. {_fmt(_epoch_to_ts(e), ktype)}")
        else:
            print(f"{sym}: CACHE-HIT (coverage={info['coverage']})")
    print(f"totals: {sum(1 for i in plan.values() if i['cache_hit'])} cache-hits, "
          f"{total_fetches} fetch span(s)")
    return total_fetches


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Warm the local K-line cache for allowlisted symbols (read-only OpenD).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--symbols", nargs="*", default=None,
                   help="override the allowlist (space-separated, e.g. 'US.SPY US.QQQ')")
    p.add_argument("--ktype", default=DEFAULT_KTYPE, choices=sorted(KTYPE_META),
                   help="K-line type (default: %(default)s)")
    p.add_argument("--start", required=True, help="start date, YYYY-MM-DD")
    p.add_argument("--end", required=True, help="end date, YYYY-MM-DD (inclusive)")
    p.add_argument("--execute", action="store_true",
                   help="actually call OpenD (default is dry-run)")
    p.add_argument("--no-fetch", action="store_true",
                   help="force dry-run even if --execute is also passed")
    p.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS,
                   help="max request_history_kline calls per window (default: %(default)s)")
    p.add_argument("--window", type=float, default=DEFAULT_WINDOW_SECONDS,
                   help="rate-limit window seconds (default: %(default)s)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    execute = args.execute and not args.no_fetch

    allowlist = _resolve_allowlist(args.symbols)
    if not allowlist:
        print("error: empty allowlist", file=sys.stderr)
        return 2

    print(f"allowlist ({len(allowlist)}): {', '.join(allowlist)}")
    if args.max_calls > 60:
        print("warning: --max-calls > 60 exceeds the official 60/30s cap", file=sys.stderr)

    # 1) Compute the plan entirely from local cache. No OpenD calls here.
    plan = _build_plan(allowlist, args.ktype, args.start, args.end)

    # 2) Print plan (dry-run default).
    _print_plan(plan, args.ktype, execute)

    if not execute:
        print("Dry-run complete: no OpenD calls were made. Pass --execute to fetch.")
        return 0

    # 3) Execute missing spans only.
    limiter = history_kline_limiter(max_calls=args.max_calls, window_seconds=args.window)
    ctx = _quote_ctx()
    try:
        _print_quota(ctx, "before")
        for sym in allowlist:
            for s, e in plan[sym]["missing"]:
                print(f"fetching {sym} {_fmt(_epoch_to_ts(s), args.ktype)} .. "
                      f"{_fmt(_epoch_to_ts(e), args.ktype)}")
                fetch_span(ctx, sym, args.ktype, s, e, limiter)
        _print_quota(ctx, "after")
    finally:
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass

    print("Warm-cache complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
