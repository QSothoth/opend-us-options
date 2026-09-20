#!/usr/bin/env python3
"""T-1 auction pulse: sample OpenD, score watchlist, print ranked table + JSON.

Examples:
  python3 run_auction_pulse.py --market HK
  python3 run_auction_pulse.py --market A --watchlist ../config/watchlist_a.json
  python3 run_auction_pulse.py --market HK --codes HK.00100,HK.09988
  python3 run_auction_pulse.py --market HK --from-capture ../fixtures/live_capture_hk.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from live_score import format_live_table, rank_watchlist  # noqa: E402
from markets import normalize_symbol  # noqa: E402
from opend_auction import (  # noqa: E402
    DEFAULT_HOST,
    DEFAULT_PORT,
    load_capture,
    sample_window,
    save_capture,
)


def parse_code_list(raw: str) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    parts = [p.strip() for p in str(raw).replace(",", " ").split() if p.strip()]
    codes: list[str] = []
    seen = set()
    for part in parts:
        _, code = normalize_symbol(part)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def load_watchlist(path: Path) -> tuple[list[str], dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    codes: list[str] = []
    names: dict[str, str] = {}
    seen = set()
    for item in data.get("symbols", []):
        if isinstance(item, str):
            raw_code, raw_name = item, ""
        else:
            raw_code = item["code"]
            raw_name = str(item.get("name") or "")
        _, code = normalize_symbol(raw_code)
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
        if raw_name:
            names[code] = raw_name
    return codes, names


def merge_universe(
    watch_codes: list[str],
    names: dict[str, str],
    extra: list[str],
) -> tuple[list[str], dict[str, str]]:
    """CLI --codes filters a watchlist; without a watchlist it is the universe."""
    if extra:
        return extra, {c: names[c] for c in extra if c in names}
    return watch_codes, names


def default_watchlist(market: str) -> Path:
    return ROOT / "config" / f"watchlist_{market.lower()}.json"


def build_payload(
    *,
    market: str,
    codes: list[str],
    series: dict,
    scores,
    host: str,
    port: int,
    captured_at: str,
    source: str,
) -> dict:
    rows = [s.to_row() for s in scores]
    return {
        "captured_at": captured_at,
        "market": market,
        "host": host,
        "port": port,
        "source": source,
        "codes": codes,
        "rows": rows,
        "scores": rows,
        "series": series,
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(description="OpenD auction T-1 strength pulse")
    p.add_argument("--market", choices=["HK", "A"], required=True)
    p.add_argument(
        "--watchlist",
        type=Path,
        default=None,
        help="JSON watchlist (default: config/watchlist_{market}.json unless --codes/--from-capture)",
    )
    p.add_argument(
        "--codes",
        default="",
        help="comma or space separated symbols; filters watchlist when both are given",
    )
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--duration", type=float, default=20.0, help="sample window seconds")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument(
        "--from-capture",
        type=Path,
        default=None,
        help="score an existing capture JSON (no OpenD); for fixtures/tests",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    extra = parse_code_list(args.codes)
    watch_codes: list[str] = []
    names: dict[str, str] = {}
    watchlist_path = args.watchlist
    if watchlist_path is None and not extra and args.from_capture is None:
        watchlist_path = default_watchlist(args.market)
    if watchlist_path is not None:
        watch_codes, names = load_watchlist(watchlist_path)

    codes, names = merge_universe(watch_codes, names, extra)
    captured_at = datetime.now().isoformat(timespec="seconds")
    host, port = args.host, args.port

    if args.from_capture is not None:
        payload_in = load_capture(str(args.from_capture))
        series_in = payload_in.get("series") or {}
        if not isinstance(series_in, dict):
            print("capture series must be an object", file=sys.stderr)
            return 2
        if not codes:
            codes = list(series_in.keys())
        series = {c: list(series_in.get(c) or []) for c in codes}
        source = f"capture:{args.from_capture}"
        captured_at = str(payload_in.get("captured_at") or captured_at)
        host = str(payload_in.get("host") or host)
        try:
            port = int(payload_in.get("port") or port)
        except (TypeError, ValueError):
            port = args.port
    else:
        if not codes:
            print("empty universe: pass --watchlist and/or --codes", file=sys.stderr)
            return 2
        series = sample_window(
            codes,
            duration_sec=args.duration,
            interval_sec=args.interval,
            host=args.host,
            port=args.port,
        )
        source = "opend"

    scores = rank_watchlist(series, market=args.market, names=names)
    payload = build_payload(
        market=args.market,
        codes=codes,
        series=series,
        scores=scores,
        host=host,
        port=port,
        captured_at=captured_at,
        source=source,
    )
    if args.out:
        save_capture(str(args.out), payload)
        print(f"wrote {args.out}", file=sys.stderr)

    print(f"auction pulse {args.market} @ {payload['captured_at']}  source={source}")
    print(format_live_table(scores))
    print()
    strong = [s for s in scores if s.label == "strong"]
    if strong:
        print("strong:", ", ".join(f"{s.code}({s.score})" for s in strong))
    else:
        print("strong: (none this pulse)")
    print()
    print(
        json.dumps(
            {
                "captured_at": payload["captured_at"],
                "market": args.market,
                "source": source,
                "rows": payload["rows"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
