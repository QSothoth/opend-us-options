#!/usr/bin/env python3
"""Label real K-line bars with interpretable market-scenario tags.

Reads local cache only (never OpenD). Rules are deterministic and documented
in ``label_day_rows`` / ``label_session_rows``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cache_store  # noqa: E402

SCENARIOS = [
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


def _ts_to_dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, unit="s", utc=True)


def label_day_rows(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """One row per trading day with multi-label scenario flags."""
    if df.empty:
        return pd.DataFrame()

    d = df.copy().sort_values("time").reset_index(drop=True)
    d["dt"] = _ts_to_dt(d["time"])
    d["date"] = d["dt"].dt.strftime("%Y-%m-%d")
    d["ret_1d"] = d["close"].pct_change()
    d["ret_5d"] = d["close"].pct_change(5)
    d["ret_20d"] = d["close"].pct_change(20)
    d["gap"] = d["open"] / d["close"].shift(1) - 1.0
    # Parkinson-ish daily range as realized vol proxy
    d["range_pct"] = (d["high"] - d["low"]) / d["close"].replace(0, np.nan)
    d["rv_20"] = d["ret_1d"].rolling(20).std() * np.sqrt(252)

    # Cross-sectional / time-series percentiles within this symbol
    p = lambda col, q: d[col].quantile(q)

    strong_up = (d["ret_20d"] >= p("ret_20d", 0.80)) & (d["ret_5d"] > 0)
    strong_dn = (d["ret_20d"] <= p("ret_20d", 0.20)) & (d["ret_5d"] < 0)
    # chop: small |ret_20d| and middling range
    abs20 = d["ret_20d"].abs()
    chop = (abs20 <= abs20.quantile(0.30)) & (d["range_pct"] <= d["range_pct"].quantile(0.50))
    gap_up = d["gap"] >= p("gap", 0.90)
    gap_dn = d["gap"] <= p("gap", 0.10)
    # V reversal: prior 3d down then day up strongly (or opposite)
    prior3 = d["close"].pct_change(3).shift(1)
    v_up = (prior3 <= prior3.quantile(0.20)) & (d["ret_1d"] >= p("ret_1d", 0.80))
    v_dn = (prior3 >= prior3.quantile(0.80)) & (d["ret_1d"] <= p("ret_1d", 0.20))
    high_vol = d["rv_20"] >= p("rv_20", 0.80)
    low_vol = d["rv_20"] <= p("rv_20", 0.20)

    out = pd.DataFrame(
        {
            "symbol": symbol,
            "grain": "day",
            "time": d["time"].astype("int64"),
            "date": d["date"],
            "session": "",
            "close": d["close"].astype("float64"),
            "ret_1d": d["ret_1d"],
            "ret_5d": d["ret_5d"],
            "ret_20d": d["ret_20d"],
            "gap": d["gap"],
            "rv_20": d["rv_20"],
            "strong_up_trend": strong_up.fillna(False).astype(bool),
            "strong_down_trend": strong_dn.fillna(False).astype(bool),
            "range_chop": chop.fillna(False).astype(bool),
            "gap_up_open": gap_up.fillna(False).astype(bool),
            "gap_down_open": gap_dn.fillna(False).astype(bool),
            "v_reversal_up": v_up.fillna(False).astype(bool),
            "v_reversal_down": v_dn.fillna(False).astype(bool),
            "late_day_spike": False,  # day grain: filled from 15m when available
            "high_vol": high_vol.fillna(False).astype(bool),
            "low_vol": low_vol.fillna(False).astype(bool),
        }
    )
    return out


def label_session_rows(df15: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Aggregate 15m bars into RTH sessions and label intraday scenarios."""
    if df15.empty:
        return pd.DataFrame()

    d = df15.copy().sort_values("time").reset_index(drop=True)
    d["dt"] = _ts_to_dt(d["time"])
    # OpenD stores naive US market time as epoch-as-UTC; hour is US session clock.
    d["hour"] = d["dt"].dt.hour
    d["minute"] = d["dt"].dt.minute
    d["date"] = d["dt"].dt.strftime("%Y-%m-%d")

    # Approximate RTH 09:30–16:00 ET
    mins = d["hour"] * 60 + d["minute"]
    rth = d[(mins >= 9 * 60 + 30) & (mins < 16 * 60)].copy()
    if rth.empty:
        rth = d.copy()

    rows = []
    for date, g in rth.groupby("date", sort=True):
        g = g.sort_values("time")
        if len(g) < 4:
            continue
        o = float(g.iloc[0]["open"])
        c = float(g.iloc[-1]["close"])
        h = float(g["high"].max())
        l = float(g["low"].min())
        # morning = first half of session bars, afternoon = second half
        mid = len(g) // 2
        am = g.iloc[:mid]
        pm = g.iloc[mid:]
        am_ret = float(am.iloc[-1]["close"] / am.iloc[0]["open"] - 1.0) if len(am) else 0.0
        pm_ret = float(pm.iloc[-1]["close"] / pm.iloc[0]["open"] - 1.0) if len(pm) else 0.0
        day_ret = c / o - 1.0 if o else 0.0
        # late-day: last ~60m (4x15m)
        late = g.tail(4)
        late_ret = float(late.iloc[-1]["close"] / late.iloc[0]["open"] - 1.0) if len(late) else 0.0
        late_range = float((late["high"].max() - late["low"].min()) / c) if c else 0.0
        # gap vs prior session close
        rows.append(
            {
                "symbol": symbol,
                "grain": "session_15m",
                "time": int(g.iloc[-1]["time"]),
                "date": date,
                "session": "RTH",
                "close": c,
                "day_ret": day_ret,
                "am_ret": am_ret,
                "pm_ret": pm_ret,
                "late_ret": late_ret,
                "late_range": late_range,
                "range_pct": (h - l) / c if c else np.nan,
                "n_bars": int(len(g)),
            }
        )

    sess = pd.DataFrame(rows)
    if sess.empty:
        return sess

    # Prior close for gap
    sess["prev_close"] = sess["close"].shift(1)
    sess["gap"] = sess["close"].iloc[:] * 0  # placeholder
    # Use open of first bar: recompute gap from 15m first open / prev close
    first_opens = []
    for date, g in rth.groupby("date", sort=True):
        g = g.sort_values("time")
        if len(g) < 4:
            continue
        first_opens.append(float(g.iloc[0]["open"]))
    sess["open"] = first_opens[: len(sess)]
    sess["gap"] = sess["open"] / sess["prev_close"] - 1.0

    p = lambda col, q: sess[col].quantile(q)
    strong_up = sess["day_ret"] >= p("day_ret", 0.80)
    strong_dn = sess["day_ret"] <= p("day_ret", 0.20)
    chop = sess["day_ret"].abs() <= sess["day_ret"].abs().quantile(0.30)
    gap_up = sess["gap"] >= p("gap", 0.90)
    gap_dn = sess["gap"] <= p("gap", 0.10)
    v_up = (sess["am_ret"] < 0) & (sess["pm_ret"] > 0) & (sess["day_ret"] > 0) & (
        sess["pm_ret"] >= p("pm_ret", 0.70)
    )
    v_dn = (sess["am_ret"] > 0) & (sess["pm_ret"] < 0) & (sess["day_ret"] < 0) & (
        sess["pm_ret"] <= p("pm_ret", 0.30)
    )
    late_spike = sess["late_range"] >= p("late_range", 0.85)
    high_vol = sess["range_pct"] >= p("range_pct", 0.80)
    low_vol = sess["range_pct"] <= p("range_pct", 0.20)

    out = pd.DataFrame(
        {
            "symbol": symbol,
            "grain": "session_15m",
            "time": sess["time"].astype("int64"),
            "date": sess["date"],
            "session": "RTH",
            "close": sess["close"].astype("float64"),
            "ret_1d": sess["day_ret"],
            "ret_5d": np.nan,
            "ret_20d": np.nan,
            "gap": sess["gap"],
            "rv_20": np.nan,
            "strong_up_trend": strong_up.fillna(False).astype(bool),
            "strong_down_trend": strong_dn.fillna(False).astype(bool),
            "range_chop": chop.fillna(False).astype(bool),
            "gap_up_open": gap_up.fillna(False).astype(bool),
            "gap_down_open": gap_dn.fillna(False).astype(bool),
            "v_reversal_up": v_up.fillna(False).astype(bool),
            "v_reversal_down": v_dn.fillna(False).astype(bool),
            "late_day_spike": late_spike.fillna(False).astype(bool),
            "high_vol": high_vol.fillna(False).astype(bool),
            "low_vol": low_vol.fillna(False).astype(bool),
        }
    )
    return out


def distribution_table(labels: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for grain, g in labels.groupby("grain"):
        n = len(g)
        for sc in SCENARIOS:
            cnt = int(g[sc].sum()) if sc in g.columns else 0
            rows.append(
                {
                    "grain": grain,
                    "scenario": sc,
                    "count": cnt,
                    "pct": round(100.0 * cnt / n, 2) if n else 0.0,
                    "n_rows": n,
                }
            )
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Label real cached K-lines with scenario tags")
    p.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    p.add_argument("--day-start", default="2022-01-01")
    p.add_argument("--day-end", default="2026-09-12")
    p.add_argument("--m15-start", default="2025-09-11")
    p.add_argument("--m15-end", default="2026-09-12")
    p.add_argument("--out", required=True, help="output scenario_labels.parquet path")
    p.add_argument("--dist-out", default=None, help="optional CSV distribution table")
    args = p.parse_args(argv)

    day_frames: List[pd.DataFrame] = []
    sess_frames: List[pd.DataFrame] = []
    for sym in args.symbols:
        day = cache_store.get_bars(sym, "K_DAY", args.day_start, args.day_end)
        day_frames.append(label_day_rows(day, sym))
        m15 = cache_store.get_bars(sym, "K_15M", args.m15_start, args.m15_end)
        sess_frames.append(label_session_rows(m15, sym))

    days = pd.concat([f for f in day_frames if f is not None and not f.empty], ignore_index=True)
    sess = pd.concat([f for f in sess_frames if f is not None and not f.empty], ignore_index=True)

    # Backfill late_day_spike onto overlapping day rows from 15m sessions.
    if not days.empty and not sess.empty and "late_day_spike" in sess.columns:
        spike = (
            sess.loc[sess["late_day_spike"], ["symbol", "date"]]
            .drop_duplicates()
            .assign(late_day_spike=True)
        )
        days = days.drop(columns=["late_day_spike"]).merge(
            spike, on=["symbol", "date"], how="left"
        )
        days["late_day_spike"] = days["late_day_spike"].fillna(False).astype(bool)

    labels = pd.concat([days, sess], ignore_index=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    labels.to_parquet(out, index=False)
    dist = distribution_table(labels)
    if args.dist_out:
        Path(args.dist_out).parent.mkdir(parents=True, exist_ok=True)
        dist.to_csv(args.dist_out, index=False)
    print(dist.to_string(index=False))
    print(f"wrote {out} rows={len(labels)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
