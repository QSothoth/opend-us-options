#!/usr/bin/env python3
"""Validate a real eval slice directory (parquet + duckdb + manifest)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

REQUIRED_SCENARIOS = [
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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    args = p.parse_args(argv)
    d = Path(args.dir)
    errors = []
    warnings = []

    for name in [
        "manifest.json",
        "klines_day.parquet",
        "klines_15m.parquet",
        "scenario_labels.parquet",
        "eval.duckdb",
        "CHECKSUMS.sha256",
        "README.md",
    ]:
        if not (d / name).exists():
            errors.append(f"missing {name}")

    if errors:
        for e in errors:
            print("ERROR:", e)
        return 1

    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    symbols = manifest.get("symbols", [])
    day = pd.read_parquet(d / "klines_day.parquet")
    m15 = pd.read_parquet(d / "klines_15m.parquet")
    labels = pd.read_parquet(d / "scenario_labels.parquet")

    for col in ["symbol", "time", "open", "high", "low", "close", "volume", "turnover"]:
        if col not in day.columns:
            errors.append(f"day missing column {col}")
        if col not in m15.columns:
            errors.append(f"15m missing column {col}")

    for sym in symbols:
        if sym not in set(day["symbol"]):
            errors.append(f"day missing symbol {sym}")
        if sym not in set(m15["symbol"]):
            errors.append(f"15m missing symbol {sym}")

    # checksums
    for line in (d / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        name = name.strip()
        if name == "CHECKSUMS.sha256":
            continue
        actual = _sha256(d / name)
        if actual != digest:
            errors.append(f"checksum mismatch {name}")

    # duckdb readable
    try:
        import duckdb

        con = duckdb.connect(str(d / "eval.duckdb"), read_only=True)
        n_day = con.execute("SELECT COUNT(*) FROM klines_day").fetchone()[0]
        n_15 = con.execute("SELECT COUNT(*) FROM klines_15m").fetchone()[0]
        n_lab = con.execute("SELECT COUNT(*) FROM scenario_labels").fetchone()[0]
        con.close()
        if n_day != len(day):
            errors.append(f"duckdb day count {n_day} != parquet {len(day)}")
        if n_15 != len(m15):
            errors.append(f"duckdb 15m count {n_15} != parquet {len(m15)}")
        if n_lab != len(labels):
            errors.append(f"duckdb labels count {n_lab} != parquet {len(labels)}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"duckdb read failed: {exc}")

    # scenario coverage
    print("== scenario distribution ==")
    for grain, g in labels.groupby("grain"):
        print(f"-- {grain} n={len(g)} --")
        for sc in REQUIRED_SCENARIOS:
            cnt = int(g[sc].sum()) if sc in g.columns else 0
            flag = "" if cnt > 0 else "  << ZERO"
            if cnt == 0:
                warnings.append(f"{grain}.{sc} has 0 samples")
            print(f"  {sc:20s} {cnt:6d}{flag}")

    # range sanity
    day_rng = manifest.get("ranges", {}).get("K_DAY", {})
    if day["time"].min() > pd.Timestamp(day_rng.get("start", "2022-01-01")).timestamp():
        # allow first trading day after start
        pass

    print(f"rows day={len(day)} 15m={len(m15)} labels={len(labels)} symbols={len(symbols)}")
    for w in warnings:
        print("WARN:", w)
    if errors:
        for e in errors:
            print("ERROR:", e)
        return 1
    print("VALIDATE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
