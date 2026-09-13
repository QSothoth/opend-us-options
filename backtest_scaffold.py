#!/usr/bin/env python3
"""Minimal cautious backtest scaffold (cache-only, no look-ahead).

Strategy
--------
Toy SMA cross on **cached daily bars only**:

* Signal on the close of day ``T``: short SMA crosses above long SMA -> BUY.
* Fill at the **open of day T+1** (never the signal-day close).
* Exit: short SMA crosses below long SMA at close of ``T`` -> SELL at open of
  ``T+1``. Any still-open position is liquidated at the final bar's close
  (there is no T+1 bar at the end of data).

No look-ahead / 未来函数
------------------------
* All decision inputs are restricted to bars with ``time <= T`` by a
  ``NoFutureBars`` guard. Any attempt to read a bar with ``time > T`` for a
  decision raises ``LookAheadError``.
* The only sanctioned future read is ``NoFutureBars.next_bar_after(T)``, used
  exclusively for the T+1 execution fill. It is never fed back into the signal.
* A built-in self-check exercises the guard before every run and aborts on
  failure.

Read-only guarantee: this module reads the local cache only. It never imports
OpenD and never places orders.

Examples
--------
    # refuses unless the cache already covers the required bars
    python backtest_scaffold.py --symbol US.SPY --start 2023-01-01 --end 2024-12-31

    # different SMA lengths
    python backtest_scaffold.py --symbol US.SPY --start 2023-01-01 --end 2024-12-31 \
        --short 10 --long 40
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import cache_store

KTYPE = "K_DAY"


class LookAheadError(RuntimeError):
    """Raised when a decision tries to read a bar after the decision time t."""


class NoFutureBars:
    """Guarded view over bars that forbids decision reads beyond time ``t``.

    Decision reads (``upto`` / ``bar_at``) are hard-checked against the current
    decision time. The single execution read (``next_bar_after``) is explicit
    and must never influence the signal.
    """

    def __init__(self, df: pd.DataFrame, time_col: str = cache_store.TIME_COL):
        self.df = df.sort_values(time_col).reset_index(drop=True)
        self.time_col = time_col
        self._t: Optional[int] = None

    def set_decision_time(self, t) -> None:
        self._t = int(t)

    def _check(self, ts) -> None:
        if self._t is not None and int(ts) > self._t:
            raise LookAheadError(
                f"look-ahead violation: bar@{int(ts)} is after decision time t={self._t}"
            )

    def upto(self) -> pd.DataFrame:
        """All bars with time <= current decision time (the only bars a decision may read)."""
        if self._t is None:
            raise RuntimeError("decision time not set; call set_decision_time() first")
        sub = self.df[self.df[self.time_col] <= self._t]
        if (sub[self.time_col] > self._t).any():
            raise LookAheadError("guard failure: upto() returned future bars")
        return sub

    def bar_at(self, t) -> pd.Series:
        """The single bar exactly at time ``t`` (decision read, must be <= current t)."""
        self._check(t)
        rows = self.df[self.df[self.time_col] == t]
        if rows.empty:
            raise KeyError(f"no bar at t={t}")
        return rows.iloc[0]

    def next_bar_after(self, t) -> Optional[pd.Series]:
        """EXECUTION read: the bar strictly after ``t``, used for the T+1 fill only."""
        rows = self.df[self.df[self.time_col] > t]
        if rows.empty:
            return None
        return rows.iloc[0]


def selfcheck_no_lookahead() -> bool:
    """Prove the guard catches future reads. Returns True on pass."""
    df = pd.DataFrame(
        {
            "time": [100, 200, 300],
            "open": [1.0, 2.0, 3.0],
            "high": [1.5, 2.5, 3.5],
            "low": [0.9, 1.9, 2.9],
            "close": [1.2, 2.2, 3.2],
            "volume": [0.0, 0.0, 0.0],
            "turnover": [0.0, 0.0, 0.0],
        }
    )
    g = NoFutureBars(df)
    g.set_decision_time(200)

    # upto() must contain only bars <= t
    assert list(g.upto()["time"]) == [100, 200], "upto() leaked a future bar"

    # reading a future bar for a decision must raise
    raised = False
    try:
        g.bar_at(300)
    except LookAheadError:
        raised = True
    if not raised:
        return False

    # the sanctioned execution read returns the T+1 bar
    nb = g.next_bar_after(200)
    if nb is None or int(nb["time"]) != 300:
        return False

    return True


def _require_cached(symbol: str, start: str, end: str, long: int) -> pd.DataFrame:
    """Refuse to run unless the required bars (incl. SMA warmup) are fully cached."""
    start_ts = pd.Timestamp(start)
    # Warmup window: need `long` trading bars before the first decision, and
    # weekdays are ~5/7 of calendar days, so double plus buffer is conservative.
    warmup_start = (start_ts - pd.Timedelta(days=int(long * 2 + 10))).strftime("%Y-%m-%d")

    missing = cache_store.missing_ranges(symbol, KTYPE, warmup_start, end)
    if missing:
        print(f"error: cache for {symbol} {KTYPE} does not fully cover "
              f"[{warmup_start}, {end}] (SMA warmup included).", file=sys.stderr)
        print("       missing spans (epoch seconds):", file=sys.stderr)
        for s, e in missing:
            print(f"         {s} .. {e}", file=sys.stderr)
        print("       warm the cache first, e.g.:", file=sys.stderr)
        print(f"         python fetch_klines.py --symbols {symbol} "
              f"--start {warmup_start} --end {end} --execute", file=sys.stderr)
        raise SystemExit(2)

    bars = cache_store.get_bars(symbol, KTYPE, warmup_start, end)
    if bars.empty:
        print(f"error: no cached bars for {symbol} {KTYPE} in [{warmup_start}, {end}]. "
              "Warm the cache first.", file=sys.stderr)
        raise SystemExit(2)
    if len(bars) < long + 1:
        print(f"error: only {len(bars)} cached bars available, need at least "
              f"{long + 1} for SMA({long}) warmup + one decision bar. "
              "Warm more history first.", file=sys.stderr)
        raise SystemExit(2)
    return bars


def run_backtest(symbol: str, short: int, long: int, start: str, end: str) -> Dict:
    """Run the SMA-cross toy strategy. Returns a result dict (trades + summary)."""
    if short >= long:
        raise SystemExit("error: --short must be < --long")

    bars = _require_cached(symbol, start, end, long)
    guard = NoFutureBars(bars)

    start_epoch = int(pd.Timestamp(start).value // 10**9)
    end_epoch = int(pd.Timestamp(end).value // 10**9)

    trades: List[dict] = []
    position = 0
    entry_price = None
    entry_time = None

    prev_short: Optional[float] = None
    prev_long: Optional[float] = None
    last_close: Optional[float] = None

    for t in bars[cache_store.TIME_COL].tolist():
        guard.set_decision_time(t)
        window = guard.upto()  # time <= t only

        if len(window) < short:
            prev_short = prev_long = None
            continue

        sma_short = float(window["close"].tail(short).mean())
        sma_long = float(window["close"].tail(long).mean()) if len(window) >= long else None
        last_close = float(window["close"].iloc[-1])

        # Warmup region: accumulate SMA state but take no decisions.
        if t < start_epoch:
            prev_short, prev_long = sma_short, sma_long
            continue

        # Past decision window: stop taking new signals (fills already used T+1).
        if t > end_epoch:
            break

        if sma_long is None or prev_short is None or prev_long is None:
            prev_short, prev_long = sma_short, sma_long
            continue

        cross_up = prev_short <= prev_long and sma_short > sma_long
        cross_down = prev_short >= prev_long and sma_short < sma_long

        # EXIT first (close existing position), then consider ENTRY.
        if position > 0 and cross_down:
            fill = guard.next_bar_after(t)  # sanctioned T+1 execution read
            if fill is None:
                break
            exit_price = float(fill["open"])
            pnl = exit_price - entry_price
            trades.append(
                {
                    "entry_time": entry_time,
                    "entry": round(entry_price, 4),
                    "exit_time": int(fill["time"]),
                    "exit": round(exit_price, 4),
                    "pnl": round(pnl, 4),
                    "pnl_pct": round(pnl / entry_price * 100.0, 4) if entry_price else 0.0,
                    "exit_reason": "cross_down",
                }
            )
            position = 0
            entry_price = entry_time = None

        if position == 0 and cross_up:
            fill = guard.next_bar_after(t)  # sanctioned T+1 execution read
            if fill is None:
                break
            entry_price = float(fill["open"])
            entry_time = int(fill["time"])
            position = 1

        prev_short, prev_long = sma_short, sma_long

    # End-of-data liquidation: mark to the final bar's close (no T+1 bar exists).
    if position > 0 and entry_price is not None and last_close is not None:
        last_t = int(bars[cache_store.TIME_COL].iloc[-1])
        pnl = last_close - entry_price
        trades.append(
            {
                "entry_time": entry_time,
                "entry": round(entry_price, 4),
                "exit_time": last_t,
                "exit": round(last_close, 4),
                "pnl": round(pnl, 4),
                "pnl_pct": round(pnl / entry_price * 100.0, 4) if entry_price else 0.0,
                "exit_reason": "end_of_data",
            }
        )

    return {
        "symbol": symbol,
        "short": short,
        "long": long,
        "start": start,
        "end": end,
        "decision_start_epoch": start_epoch,
        "decision_end_epoch": end_epoch,
        "bars_used": int(len(bars)),
        "trades": trades,
        "summary": _summarize(trades),
    }


def _summarize(trades: List[dict]) -> Dict:
    if not trades:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
                "total_pnl": 0.0, "cum_return_pct": 0.0}
    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = sum(1 for t in trades if t["pnl"] <= 0)
    total_pnl = sum(t["pnl"] for t in trades)
    capital = trades[0]["entry"]
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / len(trades) * 100.0, 2),
        "total_pnl": round(total_pnl, 4),
        "cum_return_pct": round(total_pnl / capital * 100.0, 4) if capital else 0.0,
    }


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Minimal SMA-cross backtest on cached daily K-lines (no look-ahead).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--symbol", required=True, help="e.g. US.SPY")
    p.add_argument("--start", required=True, help="decision window start, YYYY-MM-DD")
    p.add_argument("--end", required=True, help="decision window end, YYYY-MM-DD (inclusive)")
    p.add_argument("--short", type=int, default=20, help="short SMA window (default: %(default)s)")
    p.add_argument("--long", type=int, default=50, help="long SMA window (default: %(default)s)")
    p.add_argument("--selfcheck", action="store_true", help="run the look-ahead self-check and exit")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if not selfcheck_no_lookahead():
        print("error: look-ahead self-check FAILED", file=sys.stderr)
        return 3
    print("look-ahead self-check: PASS")

    if args.selfcheck:
        return 0

    result = run_backtest(args.symbol, args.short, args.long, args.start, args.end)

    print(f"\n== backtest {result['symbol']} (SMA {result['short']}/{result['long']}) ==")
    print(f"decision window: {result['start']} .. {result['end']}")
    print(f"cached bars loaded: {result['bars_used']}")

    if result["trades"]:
        print("\ntrades:")
        print(pd.DataFrame(result["trades"]).to_string(index=False))
    else:
        print("\ntrades: none")

    s = result["summary"]
    print("\nsummary:")
    print(f"  trades={s['trades']} wins={s['wins']} losses={s['losses']} "
          f"win_rate={s['win_rate_pct']}%")
    print(f"  total_pnl={s['total_pnl']} cum_return={s['cum_return_pct']}%")
    print("\nnote: fills are at the T+1 open; final liquidation is at the last bar close.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
