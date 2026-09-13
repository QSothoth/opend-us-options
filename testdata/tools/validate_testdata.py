#!/usr/bin/env python3
"""
Validate the synthetic OpenD US 0DTE fixtures.

Checks:
  1. All 14 scenarios present and per-scenario sample counts balanced (max-min <= 1).
  2. signals/*.csv schema + OHLC legality.
  3. expected/*.json schema + enum legality.
  4. events/*.jsonl exist and every line parses as a well-formed event.

Exit code 0 = pass, non-zero = fail (reasons printed to stderr).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REQUIRED_SCENARIOS = [
    "strong_up_trend",
    "strong_down_trend",
    "range_chop",
    "gap_up_open",
    "gap_down_open",
    "v_reversal_up",
    "v_reversal_down",
    "late_day_spike",
    "no_entry_signal",
    "entry_then_eod_flatten",
    "hard_stop_hit",
    "wide_spread_reject",
    "liquidity_ok_atm_pick",
    "deadline_no_fill",
]

EXIT_REASONS = {"eod", "stop", "target", "timeout", "reject", "none"}
DIRECTIONS = {"LONG", "SHORT", "NONE"}
EVENT_TYPES = {"option_snapshot", "signal", "order_ack", "fill", "reject", "flatten"}
SIGNAL_COLS = ["time", "open", "high", "low", "close", "volume", "turnover"]


def _scenario_samples(root: Path, scenarios: list[str]) -> dict[str, list[int]]:
    """Map scenario -> sorted list of variant numbers found in expected/."""
    out: dict[str, list[int]] = {}
    for sc in scenarios:
        variants = []
        for p in sorted((root / "expected").glob(f"{sc}__v*.json")):
            stem = p.stem  # e.g. strong_up_trend__v1
            suffix = stem.rsplit("__v", 1)[-1]
            if suffix.isdigit():
                variants.append(int(suffix))
        out[sc] = sorted(variants)
    return out


def check_signals(root: Path, sc: str, variant: int, errors: list[str]) -> None:
    path = root / "signals" / f"{sc}__v{variant}.csv"
    if not path.exists():
        errors.append(f"missing signals file: {path.name}")
        return
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            errors.append(f"{path.name}: no header")
            return
        if list(reader.fieldnames) != SIGNAL_COLS:
            errors.append(f"{path.name}: bad header {reader.fieldnames}, want {SIGNAL_COLS}")
            return
        n = 0
        for row in reader:
            n += 1
            try:
                t = int(row["time"])
                o = float(row["open"])
                h = float(row["high"])
                l = float(row["low"])
                c = float(row["close"])
                _v = int(row["volume"])
                _to = float(row["turnover"])
            except (ValueError, KeyError) as exc:
                errors.append(f"{path.name} row {n}: bad numeric value ({exc})")
                continue
            if h < max(o, c) or l > min(o, c):
                errors.append(f"{path.name} row {n}: OHLC illegal (h={h} o={o} l={l} c={c})")
            if l < 0 or h < l:
                errors.append(f"{path.name} row {n}: bad range (h={h} l={l})")
            if t <= 0:
                errors.append(f"{path.name} row {n}: bad time {t}")
        if n == 0:
            errors.append(f"{path.name}: empty")


def check_expected(root: Path, sc: str, variant: int, errors: list[str]) -> None:
    path = root / "expected" / f"{sc}__v{variant}.json"
    if not path.exists():
        errors.append(f"missing expected file: {path.name}")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{path.name}: invalid JSON ({exc})")
        return
    for key in ("scenario", "variant", "symbol", "direction", "should_enter", "should_exit_reason", "notes"):
        if key not in data:
            errors.append(f"{path.name}: missing key {key!r}")
    if data.get("scenario") != sc:
        errors.append(f"{path.name}: scenario field {data.get('scenario')!r} != {sc!r}")
    if data.get("variant") != variant:
        errors.append(f"{path.name}: variant field {data.get('variant')!r} != {variant}")
    if data.get("direction") not in DIRECTIONS:
        errors.append(f"{path.name}: bad direction {data.get('direction')!r}")
    if not isinstance(data.get("should_enter"), bool):
        errors.append(f"{path.name}: should_enter not bool ({data.get('should_enter')!r})")
    if data.get("should_exit_reason") not in EXIT_REASONS:
        errors.append(f"{path.name}: bad should_exit_reason {data.get('should_exit_reason')!r}")


def check_events(root: Path, sc: str, variant: int, errors: list[str]) -> None:
    path = root / "events" / f"{sc}__v{variant}.jsonl"
    if not path.exists():
        errors.append(f"missing events file: {path.name}")
        return
    n = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                ev = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{path.name} line {n}: invalid JSON ({exc})")
                continue
            if not isinstance(ev, dict):
                errors.append(f"{path.name} line {n}: not a JSON object")
                continue
            if ev.get("type") not in EVENT_TYPES:
                errors.append(f"{path.name} line {n}: bad type {ev.get('type')!r}")
            if not isinstance(ev.get("ts"), int):
                errors.append(f"{path.name} line {n}: ts missing or not int ({ev.get('ts')!r})")
            if ev.get("scenario_tag") != f"{sc}__v{variant}":
                errors.append(f"{path.name} line {n}: scenario_tag {ev.get('scenario_tag')!r} != {sc}__v{variant}")
    if n == 0:
        errors.append(f"{path.name}: empty")


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate synthetic OpenD US 0DTE fixtures")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1],
                    help="testdata root (default: parent dir of tools/)")
    args = ap.parse_args()
    root = args.out.resolve()
    errors: list[str] = []

    if not root.is_dir():
        print(f"FAIL: testdata root not found: {root}", file=sys.stderr)
        return 1

    # 1. manifest + coverage + balance
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        errors.append("missing manifest.json")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"manifest.json: invalid JSON ({exc})")
            manifest = {}
        listed = {e.get("scenario") for e in manifest.get("scenarios", []) if isinstance(e, dict)}
        missing = set(REQUIRED_SCENARIOS) - listed
        extra = listed - set(REQUIRED_SCENARIOS)
        if missing:
            errors.append(f"manifest missing scenarios: {sorted(missing)}")
        if extra:
            errors.append(f"manifest has unexpected scenarios: {sorted(extra)}")

    samples = _scenario_samples(root, REQUIRED_SCENARIOS)
    for sc in REQUIRED_SCENARIOS:
        if sc not in samples or not samples[sc]:
            errors.append(f"scenario {sc}: no variants found")
    counts = [len(v) for v in samples.values() if v]
    if counts and (max(counts) - min(counts)) > 1:
        errors.append(f"sample counts unbalanced (max-min={max(counts) - min(counts)}): {samples}")

    # 2/3/4. per-file schema checks
    for sc in REQUIRED_SCENARIOS:
        for variant in samples.get(sc, []):
            check_signals(root, sc, variant, errors)
            check_expected(root, sc, variant, errors)
            check_events(root, sc, variant, errors)

    if errors:
        print(f"FAIL: {len(errors)} problem(s) in {root}", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"PASS: {len(REQUIRED_SCENARIOS)} scenarios, {sum(counts)} samples validated in {root}")
    for sc in REQUIRED_SCENARIOS:
        print(f"  {sc}: samples={len(samples[sc])} variants={samples[sc]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
