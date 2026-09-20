"""CLI: python3 -m studies.auction_strength.code  (from repo root with PYTHONPATH=.)

Or: python3 studies/auction_strength/code/run_screen.py --fixture ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python3 code/__main__.py` from this directory
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from metrics import DayBar  # noqa: E402
from screen import format_table, load_fixture, screen_bars  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Rank auction strength vs same-day trend (A + HK)."
    )
    p.add_argument(
        "--fixture",
        type=Path,
        default=_HERE.parent / "fixtures" / "hk_2026-09-18.json",
        help="JSON fixture with bars[]",
    )
    p.add_argument("--min-auction-pct", type=float, default=0.015)
    p.add_argument("--min-day-pct", type=float, default=0.025)
    p.add_argument("--min-combined", type=float, default=55.0)
    p.add_argument("--json", action="store_true", help="print JSON rows")
    args = p.parse_args(argv)

    bars = load_fixture(args.fixture)
    result = screen_bars(
        bars,
        min_auction_pct=args.min_auction_pct,
        min_day_pct=args.min_day_pct,
        min_combined=args.min_combined,
    )
    if args.json:
        print(json.dumps(result.to_rows(), ensure_ascii=False, indent=2))
    else:
        print(format_table(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
