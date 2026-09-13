#!/usr/bin/env python3
"""
scan_put_sell_flow.py

READ-ONLY scanner for unusual LARGE SELL prints on US PUT options,
using Futu OpenD's `get_option_event` (option unusual-activity feed).

This script:
  * Connects an OpenQuoteContext to OpenD (default 127.0.0.1:11111).
  * Requests US option events filtered to PUT options.
  * Prefers a server-side TICKER_TYPE=SELL filter; if OpenD rejects that
    filter it falls back to fetching and filtering `ticker_type == 'SELL'`
    locally with pandas.
  * Filters by a minimum turnover (default 100,000 USD) and optionally by
    minimum volume and by a list of underlying owners.
  * Prints a clean table and writes /home/box/futu/option_flow/latest_put_sell.csv

It is strictly read-only:
  * Uses OpenQuoteContext only (no OpenSecTradeContext / no UnlockTrade).
  * Never calls place_order or any trading API.

Usage examples:
  python3 scan_put_sell_flow.py
  python3 scan_put_sell_flow.py --min-turnover 250000 --min-volume 100
  python3 scan_put_sell_flow.py --owners US.SKHY,US.MU,US.SNXX --pages 3
  python3 scan_put_sell_flow.py --count 300 --pages 5
"""

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

from futu import (
    RET_OK,
    EventIndicatorType,
    EventSortDir,
    OpenQuoteContext,
    OptionEventFilter,
    OptionEventSort,
    OptionMarket,
)

HOST = "127.0.0.1"
PORT = 11111
CSV_PATH = str(Path(__file__).resolve().parent / "latest_put_sell.csv")

PUT_VALUE = 2        # OPTION_TYPE value_list=[2] => PUT  ([1] => CALL)
SELL_VALUE = 2       # TICKER_TYPE value_list=[2] => SELL

# Columns we want to print / save, in order.
DISPLAY_COLUMNS = [
    "time",
    "owner",
    "option_code",
    "ticker_type",
    "option_type",
    "strike",
    "dte",
    "price",
    "volume",
    "turnover",
    "order_type_list",
    "sentiment",
]


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Read-only scanner for large SELL prints on US PUT options (Futu OpenD)."
    )
    p.add_argument(
        "--min-turnover",
        type=float,
        default=100000.0,
        help="Minimum trade turnover in USD (default: 100000).",
    )
    p.add_argument(
        "--min-volume",
        type=int,
        default=0,
        help="Minimum contract volume, applied client-side (default: 0).",
    )
    p.add_argument(
        "--owners",
        type=str,
        default="",
        help="Comma-separated owner list, e.g. US.SKHY,US.MU (OWNER_LIST filter).",
    )
    p.add_argument(
        "--count",
        type=int,
        default=200,
        help="Page size per request, OpenD allows [1, 300] (default: 200).",
    )
    p.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Maximum number of pages to fetch (default: 1).",
    )
    p.add_argument(
        "--host",
        type=str,
        default=HOST,
        help="OpenD host (default: 127.0.0.1).",
    )
    p.add_argument(
        "--port",
        type=int,
        default=PORT,
        help="OpenD port (default: 11111).",
    )
    return p.parse_args(argv)


def build_filters(args, with_ticker_type=True):
    """Build the OpenD OptionEventFilter list (AND logic)."""
    filters = [OptionEventFilter(EventIndicatorType.OPTION_TYPE, value_list=[PUT_VALUE])]

    if with_ticker_type:
        filters.append(
            OptionEventFilter(EventIndicatorType.TICKER_TYPE, value_list=[SELL_VALUE])
        )

    owners = [o.strip() for o in (args.owners or "").split(",") if o.strip()]
    if owners:
        filters.append(
            OptionEventFilter(EventIndicatorType.OWNER_LIST, security_list=owners)
        )

    if args.min_turnover is not None:
        filters.append(
            OptionEventFilter(
                EventIndicatorType.TURNOVER, interval_min=float(args.min_turnover)
            )
        )

    return filters


def fetch_events(quote_ctx, args, with_ticker_type=True):
    """
    Fetch up to `--pages` pages of events.

    Returns (ret_code, message, list_of_dataframes).
    """
    filters = build_filters(args, with_ticker_type=with_ticker_type)
    sort = OptionEventSort(EventIndicatorType.TIME, EventSortDir.DESCEND)

    frames = []
    page = ""  # first request: empty/None page
    for _ in range(max(1, args.pages)):
        ret, data = quote_ctx.get_option_event(
            OptionMarket.US_SECURITY,
            count=args.count,
            page=page or None,
            filter_list=filters,
            sort=sort,
        )
        if ret != RET_OK:
            return ret, data, frames

        frames.append(data["event_list"])
        page = data.get("next_page") or ""
        if not page:
            break

    return RET_OK, "", frames


def normalize(frames):
    """Concatenate frames and produce the display/save DataFrame."""
    if not frames:
        return pd.DataFrame(columns=DISPLAY_COLUMNS)

    raw = pd.concat(frames, ignore_index=True)
    if raw.empty:
        return pd.DataFrame(columns=DISPLAY_COLUMNS)

    out = pd.DataFrame(
        {
            "time": raw.get("fill_time"),
            "owner": raw.get("owner_code"),
            "option_code": raw.get("option_code"),
            "ticker_type": raw.get("ticker_type"),
            "option_type": raw.get("option_type"),
            "strike": raw.get("strike_price"),
            "dte": raw.get("dte"),
            "price": raw.get("price"),
            "volume": raw.get("volume"),
            "turnover": raw.get("turnover"),
            "order_type_list": raw.get("order_type_list"),
            "sentiment": raw.get("sentiment"),
        }
    )

    # Pandas-side guards in case OpenD ever returns extra rows.
    out = out[out["option_type"] == "PUT"]
    out = out[out["ticker_type"] == "SELL"]
    if out.empty:
        return out

    out["volume"] = pd.to_numeric(out["volume"], errors="coerce")
    out["turnover"] = pd.to_numeric(out["turnover"], errors="coerce")
    return out


def save_csv(df):
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    # order_type_list is a python list; join to a stable string for CSV.
    export = df.copy()
    export["order_type_list"] = export["order_type_list"].apply(
        lambda v: ",".join(v) if isinstance(v, list) else ("" if v is None else str(v))
    )
    export.to_csv(CSV_PATH, index=False)


def print_table(df):
    if df.empty:
        print("No matching large PUT SELL prints found in the fetched window.")
        return

    show = df.copy()
    show["order_type_list"] = show["order_type_list"].apply(
        lambda v: ",".join(v) if isinstance(v, list) else ("" if v is None else str(v))
    )

    with pd.option_context(
        "display.max_rows", None,
        "display.max_columns", None,
        "display.width", 250,
        "display.max_colwidth", 40,
    ):
        print(show.to_string(index=False))


def main(argv=None):
    args = parse_args(argv)

    if not (1 <= args.count <= 300):
        print("ERROR: --count must be in [1, 300] for OpenD.", file=sys.stderr)
        return 2
    if args.pages < 1:
        print("ERROR: --pages must be >= 1.", file=sys.stderr)
        return 2

    quote_ctx = None
    try:
        quote_ctx = OpenQuoteContext(host=args.host, port=args.port)
        print(
            f"Scanning US PUT SELL flow | min_turnover={args.min_turnover:,.0f} "
            f"min_volume={args.min_volume} owners={args.owners or '(all)'} "
            f"count={args.count} pages={args.pages}"
        )

        # Prefer a server-side SELL filter; fall back gracefully if unsupported.
        ret, msg, frames = fetch_events(quote_ctx, args, with_ticker_type=True)
        server_side_sell = True
        if ret != RET_OK:
            print(
                f"WARNING: TICKER_TYPE filter rejected by OpenD ({msg}); "
                "retrying and filtering SELL locally.",
                file=sys.stderr,
            )
            ret, msg, frames = fetch_events(quote_ctx, args, with_ticker_type=False)
            server_side_sell = False

        if ret != RET_OK:
            print(f"ERROR: get_option_event failed: {msg}", file=sys.stderr)
            return 1

        df = normalize(frames)

        # The local normalize() already enforces SELL; this is only informative.
        if not server_side_sell:
            print("NOTE: SELL side was filtered client-side (OpenD filter unsupported).")

        if not df.empty and args.min_volume:
            df = df[df["volume"] >= args.min_volume]

        if not df.empty:
            df = df.sort_values("turnover", ascending=False, ignore_index=True)

        print_table(df)
        save_csv(df)
        print(f"\nRows: {len(df)}  ->  saved to {CSV_PATH}")
        return 0

    except Exception as exc:  # noqa: BLE001 - surface any runtime failure
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if quote_ctx is not None:
            quote_ctx.close()
            print("Quote context closed. (No trades were placed.)")


if __name__ == "__main__":
    sys.exit(main())
