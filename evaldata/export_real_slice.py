#!/usr/bin/env python3
"""Export local OpenD K-line cache into a fixed real eval slice (Parquet + DuckDB).

Never calls OpenD. Reads only from ``cache_store``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cache_store  # noqa: E402

DEFAULT_SYMBOLS = [
    "US.SPY",
    "US.QQQ",
    "US.IWM",
    "US.AAPL",
    "US.NVDA",
    "US.TSLA",
    "US.MU",
    "US.AMD",
    "US.META",
    "US.MSFT",
]

SLICE_VERSION = "opend_us_options_eval_v1"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect(
    symbols: List[str],
    ktype: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    frames = []
    for sym in symbols:
        df = cache_store.get_bars(sym, ktype, start, end)
        if df.empty:
            raise RuntimeError(f"no cached bars for {sym} {ktype} in [{start},{end}]")
        out = df.copy()
        out.insert(0, "symbol", sym)
        out.insert(1, "ktype", ktype)
        frames.append(out)
    return pd.concat(frames, ignore_index=True)


def _write_duckdb(db_path: Path, day: pd.DataFrame, m15: pd.DataFrame, labels: Optional[pd.DataFrame]) -> None:
    import duckdb

    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE TABLE klines_day AS SELECT * FROM day")
        con.execute("CREATE TABLE klines_15m AS SELECT * FROM m15")
        if labels is not None and not labels.empty:
            con.execute("CREATE TABLE scenario_labels AS SELECT * FROM labels")
        con.execute(
            """
            CREATE VIEW v_day AS
            SELECT * FROM klines_day ORDER BY symbol, time
            """
        )
        con.execute(
            """
            CREATE VIEW v_15m AS
            SELECT * FROM klines_15m ORDER BY symbol, time
            """
        )
        if labels is not None and not labels.empty:
            con.execute(
                """
                CREATE VIEW v_scenario_counts AS
                SELECT grain, symbol,
                       SUM(CAST(strong_up_trend AS INT)) AS strong_up_trend,
                       SUM(CAST(strong_down_trend AS INT)) AS strong_down_trend,
                       SUM(CAST(range_chop AS INT)) AS range_chop,
                       SUM(CAST(gap_up_open AS INT)) AS gap_up_open,
                       SUM(CAST(gap_down_open AS INT)) AS gap_down_open,
                       SUM(CAST(v_reversal_up AS INT)) AS v_reversal_up,
                       SUM(CAST(v_reversal_down AS INT)) AS v_reversal_down,
                       SUM(CAST(late_day_spike AS INT)) AS late_day_spike,
                       SUM(CAST(high_vol AS INT)) AS high_vol,
                       SUM(CAST(low_vol AS INT)) AS low_vol,
                       COUNT(*) AS n_rows
                FROM scenario_labels
                GROUP BY 1, 2
                ORDER BY 1, 2
                """
            )
    finally:
        con.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Export real eval slice from local cache")
    p.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    p.add_argument("--day-start", default="2022-01-01")
    p.add_argument("--day-end", default="2026-09-12")
    p.add_argument("--m15-start", default="2025-09-11")
    p.add_argument("--m15-end", default="2026-09-12")
    p.add_argument("--out-dir", required=True, help="dist/.../opend_us_options_eval_v1")
    p.add_argument("--labels", default=None, help="path to scenario_labels.parquet")
    p.add_argument("--quota-before", default=None)
    p.add_argument("--quota-after", default=None)
    p.add_argument("--notes", default="", help="free-form notes for manifest")
    args = p.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    day = _collect(args.symbols, "K_DAY", args.day_start, args.day_end)
    m15 = _collect(args.symbols, "K_15M", args.m15_start, args.m15_end)

    day_path = out_dir / "klines_day.parquet"
    m15_path = out_dir / "klines_15m.parquet"
    day.to_parquet(day_path, index=False)
    m15.to_parquet(m15_path, index=False)

    labels = None
    if args.labels:
        labels = pd.read_parquet(args.labels)
        labels.to_parquet(out_dir / "scenario_labels.parquet", index=False)

    db_path = out_dir / "eval.duckdb"
    _write_duckdb(db_path, day, m15, labels)

    # per-symbol summary
    def _sym_summary(df: pd.DataFrame) -> Dict[str, dict]:
        out = {}
        for sym, g in df.groupby("symbol"):
            out[sym] = {
                "rows": int(len(g)),
                "min_time": int(g["time"].min()),
                "max_time": int(g["time"].max()),
                "min_date": str(pd.to_datetime(g["time"].min(), unit="s", utc=True).date()),
                "max_date": str(pd.to_datetime(g["time"].max(), unit="s", utc=True).date()),
            }
        return out

    scenario_dist = {}
    if labels is not None and not labels.empty:
        for grain, g in labels.groupby("grain"):
            scenario_dist[grain] = {
                sc: int(g[sc].sum())
                for sc in [
                    "strong_up_trend",
                    "strong_down_trend",
                    "range_chop",
                    "gap_up_open",
                    "gap_down_open",
                    "v_reversal_up",
                    "v_reversal_down",
                    "late_day_spike",
                    "high_vol",
                    "low_vol",
                ]
                if sc in g.columns
            }
            scenario_dist[grain]["_n_rows"] = int(len(g))

    generated_at = datetime.now(timezone.utc).isoformat()
    # Asia/Shanghai for human note
    generated_at_cst = (
        datetime.now(timezone.utc)
        .astimezone(__import__("zoneinfo").ZoneInfo("Asia/Shanghai"))
        .strftime("%Y-%m-%d %H:%M:%S CST")
    )

    manifest = {
        "version": SLICE_VERSION,
        "generated_at_utc": generated_at,
        "generated_at_cst": generated_at_cst,
        "source": "Futu OpenD request_history_kline (QFQ), local cache re-export",
        "disclaimer": (
            "Private self-use eval slice. Not for redistribution. "
            "Underlying K-lines only; option contract history path TBD / daily snapshots accumulate separately."
        ),
        "symbols": list(args.symbols),
        "ranges": {
            "K_DAY": {"start": args.day_start, "end": args.day_end},
            "K_15M": {"start": args.m15_start, "end": args.m15_end, "calendar_months": 12},
        },
        "row_counts": {"klines_day": int(len(day)), "klines_15m": int(len(m15))},
        "per_symbol": {"K_DAY": _sym_summary(day), "K_15M": _sym_summary(m15)},
        "quota": {"before": args.quota_before, "after": args.quota_after},
        "scenario_distribution": scenario_dist,
        "notes": args.notes,
        "option_contracts": "not included — OpenD has no historical option chain; path deferred",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # CHECKSUMS
    files = [
        "manifest.json",
        "klines_day.parquet",
        "klines_15m.parquet",
        "eval.duckdb",
    ]
    if (out_dir / "scenario_labels.parquet").exists():
        files.append("scenario_labels.parquet")
    lines = []
    for name in files:
        digest = _sha256_file(out_dir / name)
        lines.append(f"{digest}  {name}")
    (out_dir / "CHECKSUMS.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({"out_dir": str(out_dir), "rows_day": len(day), "rows_15m": len(m15)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
