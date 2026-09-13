#!/usr/bin/env python3
"""
Synthetic balanced fixtures generator for OpenD US 0DTE full-managed test set.

Generates 14 scenarios x 2 variants:
  - signals/{scenario}__v{1|2}.csv   (15m synthetic OHLCV bars, 26 bars 09:30-16:00 ET)
  - events/{scenario}__v{1|2}.jsonl  (option snapshot / order event traces)
  - expected/{scenario}__v{1|2}.json (expected state machine summary)
  - manifest.json                    (coverage + balance)

Fully deterministic (SEED=42). Pure synthesis: no network, no real cache reads.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import math
import random
from pathlib import Path

SEED = 42
SYMBOL = "US.SPY"
TRADE_DATE = "2024-06-21"          # a Friday -> 0DTE expiry day
EXP = "240621"                     # option code expiry YYMMDD
BAR_INTERVAL = "15m"
N_BARS = 26
BASE_PRICE = 540.0

SIGNAL_SCENARIOS = [
    "strong_up_trend",
    "strong_down_trend",
    "range_chop",
    "gap_up_open",
    "gap_down_open",
    "v_reversal_up",
    "v_reversal_down",
    "late_day_spike",
    "no_entry_signal",
]

EXEC_SCENARIOS = [
    "entry_then_eod_flatten",
    "hard_stop_hit",
    "wide_spread_reject",
    "liquidity_ok_atm_pick",
    "deadline_no_fill",
]

SCENARIOS = SIGNAL_SCENARIOS + EXEC_SCENARIOS
VARIANTS = (1, 2)

# direction / should_enter / should_exit_reason / notes
EXPECTATIONS = {
    "strong_up_trend": ("LONG", True, "eod", "Steady uptrend; program enters LONG early and flattens at EOD."),
    "strong_down_trend": ("SHORT", True, "eod", "Steady downtrend; program enters SHORT early and flattens at EOD."),
    "range_chop": ("LONG", False, "none", "Sideways chop; human direction is LONG but no timing signal fires."),
    "gap_up_open": ("LONG", True, "eod", "Gap-up open; LONG entry on the gap, flatten at EOD."),
    "gap_down_open": ("SHORT", True, "eod", "Gap-down open; SHORT entry on the gap, flatten at EOD."),
    "v_reversal_up": ("LONG", True, "eod", "Down then strong up reversal; LONG entry at the turn, flatten at EOD."),
    "v_reversal_down": ("SHORT", True, "eod", "Up then strong down reversal; SHORT entry at the turn, flatten at EOD."),
    "late_day_spike": ("LONG", True, "eod", "Flat all day then late spike; late LONG entry then EOD flatten."),
    "no_entry_signal": ("NONE", False, "none", "No directional or timing signal; no entry all day."),
    "entry_then_eod_flatten": ("LONG", True, "eod", "Clean entry then EOD flatten; full happy path."),
    "hard_stop_hit": ("SHORT", True, "stop", "Entry then adverse reversal hits hard stop."),
    "wide_spread_reject": ("LONG", False, "reject", "Order rejected: v1 wide spread, v2 zero bid."),
    "liquidity_ok_atm_pick": ("LONG", True, "eod", "Multiple strikes quoted; tight ATM picked, flatten at EOD."),
    "deadline_no_fill": ("LONG", False, "timeout", "Order accepted but no fill before deadline."),
}

# Which synthetic signal shape backs each execution scenario's "short" signals CSV
UNDERLYING_MAP = {
    "entry_then_eod_flatten": "strong_up_trend",
    "hard_stop_hit": "v_reversal_up",     # down then up -> a short gets stopped
    "wide_spread_reject": "strong_up_trend",
    "liquidity_ok_atm_pick": "strong_up_trend",
    "deadline_no_fill": "strong_up_trend",
}

# bar index at which the entry signal fires (None => no entry)
ENTRY_BAR = {
    "strong_up_trend": 2,
    "strong_down_trend": 2,
    "range_chop": None,
    "gap_up_open": 1,
    "gap_down_open": 1,
    "v_reversal_up": 14,
    "v_reversal_down": 14,
    "late_day_spike": 22,
    "no_entry_signal": None,
    "entry_then_eod_flatten": 2,
    "hard_stop_hit": 2,
    "wide_spread_reject": 2,
    "liquidity_ok_atm_pick": 3,
    "deadline_no_fill": 2,
}


def r2(x: float) -> float:
    """Round to 2 decimals with a tiny epsilon to keep halves away from banker's rounding."""
    return round(float(x) + 1e-9, 2)


def bar_times() -> list[int]:
    """26 bar open timestamps (09:30..15:45 ET, 15m), naive ET wall-clock stored as UTC epoch."""
    base = dt.datetime(2024, 6, 21, 9, 30)
    return [calendar.timegm((base + dt.timedelta(minutes=15 * i)).timetuple()) for i in range(N_BARS)]


def option_code(symbol: str, strike: float, otype: str) -> str:
    """e.g. US.SPY240621C540000 (strike 540 -> 540000)."""
    return f"{symbol}{EXP}{otype[0]}{int(round(strike * 1000))}"


def _signal_close(scenario: str, variant: int, rng: random.Random) -> list[float]:
    """Synthetic close path for the 9 signal shapes."""
    n = N_BARS
    base = BASE_PRICE
    noise = 0.05
    if scenario == "strong_up_trend":
        slope = 0.0009 if variant == 1 else 0.0016
        return [base * (1 + slope * i) + rng.gauss(0, noise) for i in range(n)]
    if scenario == "strong_down_trend":
        slope = 0.0009 if variant == 1 else 0.0016
        return [base * (1 - slope * i) + rng.gauss(0, noise) for i in range(n)]
    if scenario == "range_chop":
        amp = 1.0 if variant == 1 else 1.6
        phase = 0.0 if variant == 1 else 1.5
        return [base + amp * math.sin(i * 0.9 + phase) + rng.gauss(0, noise * 0.5) for i in range(n)]
    if scenario == "gap_up_open":
        gap = 0.012 if variant == 1 else 0.016
        drift = -0.0002 if variant == 1 else -0.0004
        start = base * (1 + gap)
        return [start * (1 + drift * i) + rng.gauss(0, noise) for i in range(n)]
    if scenario == "gap_down_open":
        gap = 0.012 if variant == 1 else 0.016
        drift = 0.0002 if variant == 1 else 0.0004
        start = base * (1 - gap)
        return [start * (1 + drift * i) + rng.gauss(0, noise) for i in range(n)]
    if scenario == "v_reversal_up":
        down = 0.0020 if variant == 1 else 0.0028
        up = 0.0035 if variant == 1 else 0.0045
        out = []
        for i in range(n):
            c = base * (1 - down * i) if i < 13 else base * (1 - down * 12) * (1 + up * (i - 12))
            out.append(c + rng.gauss(0, noise))
        return out
    if scenario == "v_reversal_down":
        up = 0.0020 if variant == 1 else 0.0028
        down = 0.0035 if variant == 1 else 0.0045
        out = []
        for i in range(n):
            c = base * (1 + up * i) if i < 13 else base * (1 + up * 12) * (1 - down * (i - 12))
            out.append(c + rng.gauss(0, noise))
        return out
    if scenario == "late_day_spike":
        spike = 0.004 if variant == 1 else 0.007
        out = []
        for i in range(n):
            c = base + rng.gauss(0, noise * 0.5)
            if i >= 22:
                c = base * (1 + spike * (i - 21))
            out.append(c)
        return out
    if scenario == "no_entry_signal":
        return [base + rng.gauss(0, noise * 0.4) for i in range(n)]
    raise ValueError(f"unknown signal shape: {scenario}")


def close_path(scenario: str, variant: int, rng: random.Random) -> list[float]:
    shape = UNDERLYING_MAP.get(scenario, scenario)
    return _signal_close(shape, variant, rng)


def build_bars(scenario: str, variant: int, rng: random.Random) -> list[dict]:
    times = bar_times()
    closes = close_path(scenario, variant, rng)
    open0 = BASE_PRICE
    if scenario in ("gap_up_open",):
        open0 = BASE_PRICE * (1 + (0.012 if variant == 1 else 0.016))
    elif scenario in ("gap_down_open",):
        open0 = BASE_PRICE * (1 - (0.012 if variant == 1 else 0.016))

    rows = []
    for i in range(N_BARS):
        c_raw = closes[i]
        o_raw = open0 if i == 0 else closes[i - 1]
        hi_raw = max(o_raw, c_raw) + abs(rng.uniform(0.03, 0.12))
        lo_raw = min(o_raw, c_raw) - abs(rng.uniform(0.03, 0.12))
        o = r2(o_raw)
        h = r2(hi_raw)
        l = max(0.01, r2(lo_raw))
        c = r2(c_raw)
        vol = int(rng.uniform(8000, 40000))
        turnover = round(vol * (o + h + l + c) / 4.0, 2)
        rows.append({
            "time": times[i],
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": vol,
            "turnover": turnover,
        })
    return rows


def _side_open(direction: str) -> str | None:
    return {"LONG": "BUY", "SHORT": "SELL"}.get(direction)


def _side_close(direction: str) -> str | None:
    return {"LONG": "SELL", "SHORT": "BUY"}.get(direction)


def make_snap_at(px: float, direction: str, strike: float, mode: str, rng: random.Random, dte: int = 0) -> dict:
    otype = "CALL" if direction == "LONG" else "PUT"
    intrinsic = max(0.0, px - strike) if otype == "CALL" else max(0.0, strike - px)
    premium = intrinsic + 0.25 + rng.uniform(0.0, 0.10)
    if mode == "tight":
        bid = max(0.0, premium - 0.025)
        ask = premium + 0.025
    elif mode == "wide":
        bid = max(0.0, premium - 0.45)
        ask = premium + 0.45
    elif mode == "zero_bid":
        bid = 0.0
        ask = premium + 0.50
    else:
        raise ValueError(f"unknown snap mode: {mode}")
    bid = r2(bid)
    ask = r2(ask)
    last = r2((bid + ask) / 2 if bid > 0 else ask)
    spread = r2(ask - bid)
    return {
        "option_code": option_code(SYMBOL, strike, otype),
        "bid": bid,
        "ask": ask,
        "last": last,
        "spread": spread,
        "strike": r2(strike),
        "option_type": otype,
        "dte": dte,
    }


def make_snap(px: float, direction: str, mode: str, rng: random.Random, dte: int = 0) -> dict:
    atm = round(px * 2) / 2
    return make_snap_at(px, direction, atm, mode, rng, dte=dte)


def _sig(ts: int, tag: str, direction: str, status: str) -> dict:
    return {"ts": ts, "type": "signal", "symbol": SYMBOL, "direction": direction,
            "status": status, "scenario_tag": tag}


def _snap(ts: int, tag: str, s: dict, side: str | None) -> dict:
    e = {"ts": ts, "type": "option_snapshot", "symbol": SYMBOL, "scenario_tag": tag, "side": side, "qty": 1, "status": "snapshot"}
    e.update(s)
    return e


def _ack(ts: int, tag: str, side: str, option_code: str | None = None) -> dict:
    e = {"ts": ts, "type": "order_ack", "symbol": SYMBOL, "scenario_tag": tag,
         "side": side, "qty": 1, "status": "accepted"}
    if option_code:
        e["option_code"] = option_code
    return e


def _fill(ts: int, tag: str, side: str, price: float, option_code: str | None = None) -> dict:
    e = {"ts": ts, "type": "fill", "symbol": SYMBOL, "scenario_tag": tag,
         "side": side, "qty": 1, "price": price, "status": "filled"}
    if option_code:
        e["option_code"] = option_code
    return e


def _reject(ts: int, tag: str, side: str, reason: str) -> dict:
    return {"ts": ts, "type": "reject", "symbol": SYMBOL, "scenario_tag": tag,
            "side": side, "qty": 1, "status": "rejected", "reason": reason}


def _flatten(ts: int, tag: str, status: str, side: str | None = None, price: float | None = None,
             reason: str | None = None, option_code: str | None = None) -> dict:
    e = {"ts": ts, "type": "flatten", "symbol": SYMBOL, "scenario_tag": tag, "status": status}
    if side:
        e["side"] = side
    if price is not None:
        e["price"] = price
    if reason:
        e["reason"] = reason
    if option_code:
        e["option_code"] = option_code
    return e


def build_events(scenario: str, variant: int, bars: list[dict], exp: dict, rng: random.Random) -> list[dict]:
    times = [b["time"] for b in bars]
    eod_ts = times[-1] + 900
    direction = exp["direction"]
    should_enter = exp["should_enter"]
    tag = f"{scenario}__v{variant}"
    entry_bar = ENTRY_BAR[scenario]
    entry_ts = times[entry_bar] if entry_bar is not None else times[0]
    px = bars[entry_bar]["close"] if entry_bar is not None else bars[-1]["close"]
    side = _side_open(direction)
    evs: list[dict] = []

    if scenario == "entry_then_eod_flatten":
        evs.append(_sig(entry_ts, tag, direction, "entry_signal"))
        s = make_snap(px, direction, "tight", rng)
        evs.append(_snap(entry_ts, tag, s, side))
        evs.append(_ack(entry_ts + 1, tag, side, s["option_code"]))
        evs.append(_fill(entry_ts + 2, tag, side, s["ask"], s["option_code"]))
        evs.append(_flatten(eod_ts, tag, "eod", _side_close(direction), s["bid"], reason="eod", option_code=s["option_code"]))
    elif scenario == "hard_stop_hit":
        stop_bar = 15 if variant == 1 else 17
        e_ts = times[2]
        px_e = bars[2]["close"]
        evs.append(_sig(e_ts, tag, direction, "entry_signal"))
        s = make_snap(px_e, direction, "tight", rng)
        evs.append(_snap(e_ts, tag, s, side))
        evs.append(_ack(e_ts + 1, tag, side, s["option_code"]))
        evs.append(_fill(e_ts + 2, tag, side, s["ask"], s["option_code"]))
        s2 = make_snap(bars[stop_bar]["close"], direction, "tight", rng)
        evs.append(_flatten(times[stop_bar], tag, "stop", _side_close(direction), s2["bid"], reason="hard_stop", option_code=s2["option_code"]))
    elif scenario == "wide_spread_reject":
        evs.append(_sig(entry_ts, tag, direction, "entry_signal"))
        mode = "wide" if variant == 1 else "zero_bid"
        s = make_snap(px, direction, mode, rng)
        evs.append(_snap(entry_ts, tag, s, side))
        reason = "wide_spread" if mode == "wide" else "zero_bid"
        evs.append(_reject(entry_ts + 1, tag, side, reason))
    elif scenario == "liquidity_ok_atm_pick":
        evs.append(_sig(entry_ts, tag, direction, "entry_signal"))
        atm = round(px * 2) / 2
        strikes = [atm - 1.0, atm, atm + 1.0] if variant == 1 else [atm - 2.0, atm, atm + 2.0]
        for k, mode in zip(strikes, ["wide", "tight", "wide"]):
            evs.append(_snap(entry_ts, tag, make_snap_at(px, direction, k, mode, rng), side))
        s_atm = make_snap_at(px, direction, atm, "tight", rng)
        evs.append(_ack(entry_ts + 1, tag, side, s_atm["option_code"]))
        evs.append(_fill(entry_ts + 2, tag, side, s_atm["ask"], s_atm["option_code"]))
        evs.append(_flatten(eod_ts, tag, "eod", _side_close(direction), s_atm["bid"], reason="eod", option_code=s_atm["option_code"]))
    elif scenario == "deadline_no_fill":
        evs.append(_sig(entry_ts, tag, direction, "entry_signal"))
        s = make_snap(px, direction, "tight", rng)
        evs.append(_snap(entry_ts, tag, s, side))
        evs.append(_ack(entry_ts + 1, tag, side, s["option_code"]))
        evs.append(_flatten(eod_ts, tag, "timeout", reason="deadline_no_fill", option_code=s["option_code"]))
    else:
        # signal-side scenarios: a reference trace consistent with expected outcome
        if should_enter:
            evs.append(_sig(entry_ts, tag, direction, "entry_signal"))
            s = make_snap(px, direction, "tight", rng)
            evs.append(_snap(entry_ts, tag, s, side))
            evs.append(_ack(entry_ts + 1, tag, side, s["option_code"]))
            evs.append(_fill(entry_ts + 2, tag, side, s["ask"], s["option_code"]))
            evs.append(_flatten(eod_ts, tag, "eod", _side_close(direction), s["bid"], reason="eod", option_code=s["option_code"]))
        else:
            evs.append(_sig(entry_ts, tag, direction, "no_entry"))
    return evs


def build_expected(scenario: str, variant: int) -> dict:
    direction, should_enter, exit_reason, notes = EXPECTATIONS[scenario]
    return {
        "scenario": scenario,
        "variant": variant,
        "symbol": SYMBOL,
        "direction": direction,
        "should_enter": should_enter,
        "should_exit_reason": exit_reason,
        "notes": notes,
    }


def write_csv(path: Path, bars: list[dict]) -> None:
    header = "time,open,high,low,close,volume,turnover"
    lines = [header]
    for b in bars:
        lines.append(f"{b['time']},{b['open']:.2f},{b['high']:.2f},{b['low']:.2f},{b['close']:.2f},{b['volume']},{b['turnover']:.2f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in events) + "\n", encoding="utf-8")


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(root: Path) -> dict:
    (root / "signals").mkdir(parents=True, exist_ok=True)
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "expected").mkdir(parents=True, exist_ok=True)

    manifest_scenarios = []
    for si, sc in enumerate(SCENARIOS):
        entry = {"scenario": sc, "sample_count": 0, "variants": [], "signals": [], "events": [], "expected": []}
        for variant in VARIANTS:
            rng = random.Random(SEED * 1000 + si * 10 + variant)
            bars = build_bars(sc, variant, rng)
            exp = build_expected(sc, variant)
            evs = build_events(sc, variant, bars, exp, rng)

            write_csv(root / "signals" / f"{sc}__v{variant}.csv", bars)
            write_jsonl(root / "events" / f"{sc}__v{variant}.jsonl", evs)
            write_json(root / "expected" / f"{sc}__v{variant}.json", exp)

            entry["sample_count"] += 1
            entry["variants"].append(variant)
            entry["signals"].append(f"signals/{sc}__v{variant}.csv")
            entry["events"].append(f"events/{sc}__v{variant}.jsonl")
            entry["expected"].append(f"expected/{sc}__v{variant}.json")
        manifest_scenarios.append(entry)

    counts = [e["sample_count"] for e in manifest_scenarios]
    manifest = {
        "seed": SEED,
        "synthetic": True,
        "symbol": SYMBOL,
        "trade_date": TRADE_DATE,
        "expiry": EXP,
        "bar_interval": BAR_INTERVAL,
        "bar_count_per_signal": N_BARS,
        "bars_schema": ["time", "open", "high", "low", "close", "volume", "turnover"],
        "time_convention": "naive ET wall-clock stored as int64 epoch seconds (UTC)",
        "scenario_count": len(SCENARIOS),
        "total_samples": len(SCENARIOS) * len(VARIANTS),
        "balance": {
            "max_samples": max(counts),
            "min_samples": min(counts),
            "diff": max(counts) - min(counts),
            "balanced": (max(counts) - min(counts)) <= 1,
        },
        "scenarios": manifest_scenarios,
    }
    write_json(root / "manifest.json", manifest)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate synthetic balanced OpenD US 0DTE fixtures")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1],
                    help="testdata root (default: parent dir of tools/)")
    args = ap.parse_args()
    root = args.out.resolve()
    manifest = build(root)
    print(f"scenarios={manifest['scenario_count']}")
    print(f"total_samples={manifest['total_samples']}")
    print(f"balance={manifest['balance']}")
    for e in manifest["scenarios"]:
        print(f"  {e['scenario']}: samples={e['sample_count']} variants={e['variants']}")
    print(f"output={root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
