#!/usr/bin/env python3
"""T-1 auction pulse: sample OpenD, score watchlist, print ranked table.

Examples:
  python3 run_auction_pulse.py --market HK --watchlist ../config/watchlist_hk.json
  python3 run_auction_pulse.py --market A --watchlist ../config/watchlist_a.json \\
      --duration 25 --host 127.0.0.1 --port 11111 --out /tmp/hk_auction.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from live_score import format_live_table, rank_watchlist  # noqa: E402
from opend_auction import sample_window, save_capture  # noqa: E402


def load_watchlist(path: Path) -> tuple[list[str], dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    codes = []
    names = {}
    for item in data.get("symbols", []):
        if isinstance(item, str):
            codes.append(item)
        else:
            codes.append(item["code"])
            if item.get("name"):
                names[item["code"]] = item["name"]
    return codes, names


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="OpenD auction T-1 strength pulse")
    p.add_argument("--market", choices=["HK", "A"], required=True)
    p.add_argument("--watchlist", type=Path, required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=11111)
    p.add_argument("--duration", type=float, default=20.0, help="sample window seconds")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)

    codes, names = load_watchlist(args.watchlist)
    if not codes:
        print("empty watchlist", file=sys.stderr)
        return 2

    series = sample_window(
        codes,
        duration_sec=args.duration,
        interval_sec=args.interval,
        host=args.host,
        port=args.port,
    )
    scores = rank_watchlist(series, market=args.market, names=names)
    payload = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "market": args.market,
        "host": args.host,
        "port": args.port,
        "codes": codes,
        "series": series,
        "scores": [s.__dict__ for s in scores],
    }
    if args.out:
        save_capture(str(args.out), payload)
        print(f"wrote {args.out}", file=sys.stderr)

    print(f"auction pulse {args.market} @ {payload['captured_at']}")
    print(format_live_table(scores))
    print()
    strong = [s for s in scores if s.label == "strong"]
    if strong:
        print("strong:", ", ".join(f"{s.code}({s.score})" for s in strong))
    else:
        print("strong: (none this pulse)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
