#!/usr/bin/env python3
"""
scan_hk_option_flow.py

READ-ONLY scanner for Hong Kong listed equity options via Futu OpenD.

Primary source (`--source rank`, default):
  OpenQuoteContext.get_option_rank(OptionMarket.HK_SECURITY, ...)
  Daily near-expiry turnover / volume ranking. Includes contracts that later
  expired, so it is the right feed for 日频 / 回测.

Optional source (`--source event`):
  get_option_event unusual-print feed. Live tick clusters only. OpenD purges
  expired near-expiry history, so long event backtests look empty.

This script:
  * Connects an OpenQuoteContext to OpenD (default 127.0.0.1:11111).
  * Rank mode: pages get_option_rank by TURNOVER/VOLUME, validates the
    returned trading_date, computes DTE from option-code YYMMDD vs the
    ranking day (never OpenD LEFT_DAYS / dte).
  * Event mode: unusual prints + tick clusters (legacy live layer).
  * Live: dumps the latest session near-expiry table.
  * Backtest: owner-day rank signals (or event clusters) + daily rollup.

It is strictly read-only:
  * Uses OpenQuoteContext only (no OpenSecTradeContext / no UnlockTrade).
  * Never calls place_order or any trading API.

Usage examples:
  python3 scan_hk_option_flow.py --backtest --source rank --preset aggressive
  python3 scan_hk_option_flow.py --source rank --max-dte 5 --side CALL
  python3 scan_hk_option_flow.py --source event --min-turnover 200000 --max-dte 5
  python3 scan_hk_option_flow.py --backtest --source event --preset pi
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from futu import (
    RET_ERROR,
    RET_OK,
    EventIndicatorType,
    EventSortDir,
    KLType,
    Market,
    OpenQuoteContext,
    OptionEventFilter,
    OptionEventSort,
    OptionMarket,
    OptionRankFilter,
    OptionRankIndicatorType,
    OptionRankType,
    SecurityType,
    TradeDateMarket,
)

HOST = "127.0.0.1"
PORT = 11111
SCRIPT_DIR = Path(__file__).resolve().parent
LIVE_CSV_NAME = "latest_hk_option_flow.csv"
RAW_CSV_NAME = "hk_option_events_raw.csv"
RANK_RAW_CSV_NAME = "hk_option_rank_raw.csv"
ALERTS_CSV_NAME = "hk_option_alerts.csv"
ROLLUP_CSV_NAME = "hk_option_daily_rollup.csv"

# OpenD exact-value enums (see Qot_GetOptionEvent.proto / OptionType).
CALL_VALUE = 1
PUT_VALUE = 2
BUY_VALUE = 1
SELL_VALUE = 2
NEUTRAL_VALUE = 3

SIDE_VALUES = {"CALL": CALL_VALUE, "PUT": PUT_VALUE}
ACTION_VALUES = {"BUY": BUY_VALUE, "SELL": SELL_VALUE, "NEUTRAL": NEUTRAL_VALUE}

RANK_TYPE_MAP = {
    "TURNOVER": OptionRankType.TURNOVER,
    "VOLUME": OptionRankType.VOLUME,
    "OI": OptionRankType.OI,
    "OI_INCREMENT": OptionRankType.OI_INCREMENT,
}

# Earliest rank history verified on this OpenD box (2026-09-20).
# Days before this may silently return the latest ranking — always validate.
RANK_HIST_START = date(2026, 8, 7)

# Known MiniMax lead the backtest report always checks.
FOCUS_OWNER = "HK.00100"
FOCUS_NAME = "MiniMax"
FOCUS_ROOT = "MNX"
FOCUS_FROM = date(2026, 9, 15)
FOCUS_TO = date(2026, 9, 18)
FOCUS_MORNING = date(2026, 9, 16)
FOCUS_CONTRACT_NEEDLE = "260918C235"  # MNX260918C235 / HK.MNX260918C235000

OPTION_YYMMDD_RE = re.compile(r"(\d{6})[CPcp]")
OPTION_CODE_RE = re.compile(r"^(?:HK\.)?([A-Za-z]+)(\d{6})([CPcp])(\d+)$")

# Fallback root -> owner when basicinfo is missing (expired contracts).
ROOT_OWNER_FALLBACK = {
    "AAC": "HK.02018",
    "AKS": "HK.09926",
    "ALB": "HK.09988",
    "ALC": "HK.02600",
    "ALH": "HK.00241",
    "ANA": "HK.02020",
    "BIU": "HK.09888",
    "BOC": "HK.02388",
    "BYD": "HK.01211",
    "BYE": "HK.00285",
    "CAT": "HK.03750",
    "CHT": "HK.00941",
    "CHU": "HK.00762",
    "CLI": "HK.02628",
    "CMB": "HK.03968",
    "CNC": "HK.00883",
    "COL": "HK.00688",
    "CPC": "HK.00386",
    "CPI": "HK.02601",
    "CRL": "HK.01109",
    "CSE": "HK.01088",
    "CSP": "HK.01093",
    "CTC": "HK.00728",
    "CTS": "HK.06030",
    "GAH": "HK.00175",
    "GLI": "HK.01772",
    "GLX": "HK.00027",
    "GWM": "HK.02333",
    "HDO": "HK.06862",
    "HEH": "HK.00006",
    "HKB": "HK.00005",
    "HKG": "HK.00003",
    "HNP": "HK.00902",
    "HOS": "HK.01347",
    "HRB": "HK.09660",
    "JDH": "HK.06618",
    "JXC": "HK.00358",
    "KAT": "HK.02513",
    "KDS": "HK.00268",
    "KEH": "HK.02423",
    "KSO": "HK.03888",
    "KST": "HK.01024",
    "LAO": "HK.06181",
    "LAU": "HK.02015",
    "LEN": "HK.00992",
    "LNI": "HK.02331",
    "MDG": "HK.00300",
    "MEN": "HK.02319",
    "MET": "HK.03690",
    "MIU": "HK.01810",
    "MNX": "HK.00100",
    "MOL": "HK.03993",
    "MSB": "HK.01988",
    "NBM": "HK.03323",
    "NCL": "HK.01336",
    "NIO": "HK.09866",
    "NTE": "HK.09999",
    "NWD": "HK.00017",
    "PAI": "HK.02318",
    "PEC": "HK.00857",
    "PEN": "HK.09868",
    "PHT": "HK.01833",
    "PIN": "HK.01339",
    "POP": "HK.09992",
    "SAN": "HK.01928",
    "SGM": "HK.01787",
    "SHN": "HK.00625",
    "SHZ": "HK.02313",
    "SMC": "HK.00981",
    "SNO": "HK.02382",
    "TCH": "HK.00700",
    "TRP": "HK.09961",
    "UBR": "HK.09880",
    "WCP": "HK.02338",
    "XAB": "HK.01288",
    "XBC": "HK.03988",
    "XCC": "HK.00939",
    "XIC": "HK.01398",
    "YZC": "HK.01171",
    "ZJG": "HK.02259",
    "ZJI": "HK.03308",
    "ZJM": "HK.02899",
    "ZMI": "HK.01818",
}

OWNER_NAME_FALLBACK = {
    "HK.00100": "MiniMax",
    "HK.00700": "腾讯控股",
    "HK.02513": "Z.AI",
    "HK.09988": "阿里巴巴-W",
    "HK.09868": "小鹏-W",
    "HK.06181": "老铺黄金",
    "HK.00992": "联想集团",
    "HK.00939": "建设银行",
    "HK.03750": "宁德时代",
    "HK.02628": "中国人寿",
    "HK.03690": "美团-W",
    "HK.01810": "小米集团-W",
    "HK.09992": "泡泡玛特",
}

DISPLAY_COLUMNS = [
    "time",
    "owner",
    "option_code",
    "ticker_type",
    "option_type",
    "strike",
    "expiry",
    "dte_at_fill",
    "price",
    "volume",
    "turnover",
    "underlying",
    "sentiment",
]

ALERT_COLUMNS = DISPLAY_COLUMNS + [
    "alert_reason",
    "cluster_id",
    "cluster_prints",
    "cluster_turnover",
]

RANK_LIVE_COLUMNS = [
    "trading_date",
    "owner",
    "owner_name",
    "code",
    "name",
    "option_type",
    "strike",
    "expiry",
    "dte",
    "volume",
    "turnover",
    "oi_increment",
    "open_interest",
    "iv",
    "change_ratio",
    "option_price",
]

RANK_ALERT_COLUMNS = [
    "trading_date",
    "owner",
    "owner_name",
    "lead_code",
    "lead_name",
    "option_type",
    "strike",
    "expiry",
    "dte",
    "volume",
    "turnover",
    "owner_turnover",
    "owner_volume",
    "n_contracts",
    "oi_increment",
    "iv",
    "change_ratio",
    "day_chg_pct",
    "open_chg_pct",
    "ret_1d",
    "ret_2d",
    "alert_reason",
]

# Factory defaults (used when --preset is omitted). Preset values below
# override any dest that the user did not pass on the CLI.
FACTORY_DEFAULTS = {
    "min_turnover": 200000.0,
    "max_dte": 5,
    "cluster_window_min": 30.0,
    "cluster_min_prints": 3,
    "cluster_min_turnover": 1500000.0,
    "cluster_only": False,
    "same_strike_cluster": False,
    "require_stock_not_up": None,
    "min_underlying_chg": None,
    "min_buy_sell_ratio": None,
}

# Rank-mode daily-signal defaults. Cluster flags are unused here.
RANK_FACTORY_DEFAULTS = {
    "min_turnover": 200000.0,
    "min_owner_turnover": 500000.0,
    "min_volume": 0,
    "min_oi_increment": None,
    "max_dte": 5,
    "min_underlying_chg": None,
    "require_stock_not_up": None,
    "rank_top": 400,
}

PRESETS = {
    "default": dict(FACTORY_DEFAULTS),
    # Near-expiry cluster only; modest DTE relax to catch Friday next-week.
    # --require-stock-not-up 8 keeps the 9/16 MiniMax lead (+5%) and drops
    # already-extended prints.
    "conservative": {
        "min_turnover": 200000.0,
        "max_dte": 7,
        "cluster_window_min": 30.0,
        "cluster_min_prints": 3,
        "cluster_min_turnover": 1500000.0,
        "cluster_only": True,
        "same_strike_cluster": False,
        "require_stock_not_up": 8.0,
    },
    # Still cluster-required; DTE 14 and 2 prints / 1M to raise hit count.
    # Do not go to DTE 21 — monthly dumps diluted 1d win rate in the study.
    "aggressive": {
        "min_turnover": 200000.0,
        "max_dte": 14,
        "cluster_window_min": 30.0,
        "cluster_min_prints": 2,
        "cluster_min_turnover": 1000000.0,
        "cluster_only": True,
        "same_strike_cluster": False,
        "require_stock_not_up": None,
    },
    # Pi angle: same cluster shape as `aggressive`, but require the underlying
    # to already be UP at fill (causal: prior close + fill-time price only).
    # In the 365d replay chg>0 drops the three down-trend MiniMax days
    # (9/11, 9/14, 9/15). On the broad DTE<=14 size universe this split has
    # mean(ret_1d) diff +7.0% with permutation p=0.008, and it decays above
    # DTE~21, which is why 14 stays the cap. See pi-improve/REPORT.md.
    "pi": {
        "min_turnover": 200000.0,
        "max_dte": 14,
        "cluster_window_min": 30.0,
        "cluster_min_prints": 2,
        "cluster_min_turnover": 1000000.0,
        "cluster_only": True,
        "same_strike_cluster": False,
        "require_stock_not_up": None,
        "min_underlying_chg": 0.0,
        "min_buy_sell_ratio": None,
    },
}

# Rank presets: same names, remapped onto owner-day near-expiry turnover
# (not tick clusters). Thresholds are HKD day totals from get_option_rank.
RANK_PRESETS = {
    "default": dict(RANK_FACTORY_DEFAULTS),
    # Stricter size + require the stock's ranking-day close >= prior close.
    "conservative": {
        "min_turnover": 500000.0,
        "min_owner_turnover": 1500000.0,
        "min_volume": 0,
        "min_oi_increment": None,
        "max_dte": 7,
        "min_underlying_chg": 0.0,
        "require_stock_not_up": None,
        "rank_top": 400,
    },
    # Looser size, DTE cap 14 (do not go to 21).
    "aggressive": {
        "min_turnover": 200000.0,
        "min_owner_turnover": 1000000.0,
        "min_volume": 0,
        "min_oi_increment": None,
        "max_dte": 14,
        "min_underlying_chg": None,
        "require_stock_not_up": None,
        "rank_top": 400,
    },
    # Aggressive shape + ranking-day close already up vs prior close (EOD,
    # no look-ahead into ret_1d). Analog of the event-mode fill-time pi gate.
    "pi": {
        "min_turnover": 200000.0,
        "min_owner_turnover": 1000000.0,
        "min_volume": 0,
        "min_oi_increment": None,
        "max_dte": 14,
        "min_underlying_chg": 0.0,
        "require_stock_not_up": None,
        "rank_top": 400,
    },
}


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=(
            "Read-only HK listed equity-option scanner. "
            "Default source is get_option_rank (daily near-expiry). "
            "--source event keeps get_option_event tick clusters (live only; "
            "expired near-expiry history is purged)."
        )
    )
    p.add_argument(
        "--source",
        type=str,
        default="rank",
        choices=["rank", "event"],
        help=(
            "rank (default): get_option_rank daily ranking, includes expired "
            "contracts. event: get_option_event unusual prints (live tick "
            "clusters; historical near-expiry is incomplete)."
        ),
    )
    p.add_argument(
        "--preset",
        type=str,
        default=None,
        choices=sorted(PRESETS.keys()),
        help=(
            "Named threshold bundle: default | conservative | aggressive | pi. "
            "Rank mode remaps these onto owner-day turnover gates; event mode "
            "keeps the original tick-cluster mapping. Explicit flags override."
        ),
    )
    p.add_argument(
        "--min-turnover",
        type=float,
        default=None,
        help=(
            "Rank: minimum single-contract day turnover HKD. "
            "Event: minimum print turnover HKD (default: 200000)."
        ),
    )
    p.add_argument(
        "--min-owner-turnover",
        type=float,
        default=None,
        help=(
            "Rank only: minimum summed near-expiry matching-side turnover "
            "on the same owner+day (default: 500000; 0 disables)."
        ),
    )
    p.add_argument(
        "--min-volume",
        type=int,
        default=None,
        help="Minimum contract volume, applied client-side (default: 0).",
    )
    p.add_argument(
        "--min-oi-increment",
        type=float,
        default=None,
        help="Rank only: require lead (or any) contract oi_increment >= N. 0/omit = off.",
    )
    p.add_argument(
        "--max-dte",
        type=int,
        default=None,
        help=(
            "Keep rows whose computed DTE vs ranking day (rank) or fill "
            "(event) is in [0, MAX] (default: 5). Do not use OpenD LEFT_DAYS."
        ),
    )
    p.add_argument(
        "--rank-type",
        type=str,
        default="TURNOVER",
        choices=sorted(RANK_TYPE_MAP.keys()),
        help="get_option_rank sort (default: TURNOVER).",
    )
    p.add_argument(
        "--rank-top",
        type=int,
        default=None,
        help="Rank: max contracts to page per day (default: 400).",
    )
    p.add_argument(
        "--sleep",
        type=float,
        default=0.55,
        help="Minimum seconds between OpenD calls (default: 0.55; ~60/30s cap).",
    )
    p.add_argument(
        "--side",
        type=str,
        default="CALL",
        choices=["CALL", "PUT", "ALL"],
        help="Option type filter (default: CALL).",
    )
    p.add_argument(
        "--action",
        type=str,
        default="BUY",
        choices=["BUY", "SELL", "NEUTRAL", "ALL"],
        help="Aggressor / ticker_type filter (default: BUY).",
    )
    p.add_argument(
        "--owners",
        type=str,
        default="",
        help="Comma-separated owner list, e.g. HK.00100,HK.00700 (OWNER_LIST filter).",
    )
    p.add_argument(
        "--count",
        type=int,
        default=200,
        help="Page size per request, OpenD allows [1, 300] (default: 200; 300 in --backtest).",
    )
    p.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Maximum number of pages to fetch (default: 1; 5 in --backtest).",
    )
    p.add_argument(
        "--max-day-num",
        type=int,
        default=None,
        help=(
            "OpenD MAX_DAY_NUM lookback: 0=today, 1=last 2 days, N=last N+1 days. "
            "Default: omitted in live mode, 7 in --backtest."
        ),
    )
    p.add_argument(
        "--since",
        type=str,
        default="",
        help="Client-side inclusive fill date YYYY-MM-DD (event source).",
    )
    p.add_argument(
        "--until",
        type=str,
        default="",
        help="Client-side inclusive fill date YYYY-MM-DD (event source).",
    )
    p.add_argument(
        "--from",
        dest="from_date",
        type=str,
        default="",
        help=(
            "Rank inclusive start YYYY-MM-DD (default: earliest verified "
            f"history {RANK_HIST_START.isoformat()}). Alias of --since."
        ),
    )
    p.add_argument(
        "--to",
        dest="to_date",
        type=str,
        default="",
        help="Rank inclusive end YYYY-MM-DD (default: last HK session). Alias of --until.",
    )
    p.add_argument(
        "--backtest",
        action="store_true",
        help=(
            "Replay / report mode. Rank (default): owner-day near-expiry "
            "signals + forward day-K returns. Event: tick clusters."
        ),
    )
    p.add_argument(
        "--cluster-window-min",
        type=float,
        default=None,
        help="Cluster time-span window in minutes (default: 30).",
    )
    p.add_argument(
        "--cluster-min-prints",
        type=int,
        default=None,
        help="Cluster fires if print count >= this (default: 3).",
    )
    p.add_argument(
        "--cluster-min-turnover",
        type=float,
        default=None,
        help="Cluster fires if summed turnover >= this HKD (default: 1500000).",
    )
    p.add_argument(
        "--cluster-only",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Alert only on qualifying clusters (ignore size-only prints). "
            "conservative/aggressive presets enable this. "
            "Use --no-cluster-only to force size-or-cluster."
        ),
    )
    p.add_argument(
        "--same-strike-cluster",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Cluster only prints that share the same strike (same-strike streak). "
            "Default: off (any strikes, same owner + expiry week + day)."
        ),
    )
    p.add_argument(
        "--min-underlying-chg",
        nargs="?",
        const=0.0,
        type=float,
        default=None,
        help=(
            "Skip alerts whose underlying is DOWN/up less than N%% vs prior "
            "close at fill (pass flag alone for 0 = require up at fill). "
            "Causal: uses prior close + fill-time price only. Enabled by "
            "--preset pi. Pass a very negative number to disable."
        ),
    )
    p.add_argument(
        "--min-buy-sell-ratio",
        type=float,
        default=None,
        help=(
            "Near-expiry same-side imbalance gate: keep an owner+day only if "
            "near-expiry BUY turnover >= RATIO * near-expiry SELL turnover "
            "(days with zero SELL always pass). 1.0 = buy>sell. 0 disables. "
            "Adds one extra get_option_event call for the opposite aggressor. "
            "RESEARCH/BACKTEST ONLY: the full-day SELL total includes prints "
            "after the BUY cluster (look-ahead); a causal version adds no "
            "signal. NOT enabled by --preset pi."
        ),
    )
    p.add_argument(
        "--require-stock-not-up",
        nargs="?",
        const=8.0,
        type=float,
        default=None,
        help=(
            "Skip alerts where underlying is already up more than N%% vs prior "
            "close at fill (pass flag alone for 8). Default: off. "
            "Pass 0 to disable a preset that turns it on."
        ),
    )
    p.add_argument(
        "--out-dir",
        type=str,
        default="",
        help="Directory for CSV output (default: this script's directory).",
    )
    p.add_argument(
        "--csv",
        type=str,
        default="",
        help="Live-mode CSV path (default: <out-dir>/latest_hk_option_flow.csv).",
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


def apply_preset_and_defaults(args):
    """Fill None dests from --preset, then from factory defaults."""
    source = getattr(args, "source", "rank") or "rank"
    if source == "rank":
        bundle = RANK_PRESETS.get(args.preset or "default", RANK_FACTORY_DEFAULTS)
        factory = RANK_FACTORY_DEFAULTS
        other = FACTORY_DEFAULTS
    else:
        bundle = PRESETS.get(args.preset or "default", FACTORY_DEFAULTS)
        factory = FACTORY_DEFAULTS
        other = RANK_FACTORY_DEFAULTS
    for key, value in bundle.items():
        if getattr(args, key, None) is None:
            setattr(args, key, value)
    for key, value in factory.items():
        if getattr(args, key, None) is None:
            setattr(args, key, value)
    for key, value in other.items():
        if getattr(args, key, None) is None:
            setattr(args, key, value)
    # 0 means "off" so a preset's --require-stock-not-up 8 can be cancelled.
    if args.require_stock_not_up is not None and float(args.require_stock_not_up) <= 0:
        args.require_stock_not_up = None
    # 0 means "off" so a preset's imbalance gate can be cancelled from the CLI.
    if args.min_buy_sell_ratio is not None and float(args.min_buy_sell_ratio) <= 0:
        args.min_buy_sell_ratio = None
    if args.min_oi_increment is not None and float(args.min_oi_increment) <= 0:
        args.min_oi_increment = None
    return args


def apply_mode_defaults(args):
    """Backtest wants a full lookback page; live stays a latest-page dump."""
    if args.source == "rank":
        # OpenD get_option_rank count is [1, 200].
        if args.count > 200:
            print(
                f"NOTE: --count {args.count} exceeds rank API max 200; clamping.",
                file=sys.stderr,
            )
            args.count = 200
        if args.backtest and args.pages == 1:
            args.pages = 2
        if args.rank_top is None:
            args.rank_top = 400
        if args.rank_top < 1:
            args.rank_top = 200
    elif args.backtest:
        if args.count == 200:
            args.count = 300
        if args.pages == 1:
            args.pages = 5
        if args.max_day_num is None:
            args.max_day_num = 7
    return args


def parse_owners(owners_str):
    return [o.strip() for o in (owners_str or "").split(",") if o.strip()]


def parse_ymd(value, flag):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit(f"ERROR: {flag} must be YYYY-MM-DD, got {value!r}") from exc


def build_filters(args, with_ticker_type=True, action=None):
    """Build the OpenD OptionEventFilter list (AND logic).

    `action` overrides args.action so the imbalance gate can fetch the
    opposite aggressor (SELL) without mutating the parsed arguments.
    """
    action = args.action if action is None else action
    filters = []

    if args.side != "ALL":
        filters.append(
            OptionEventFilter(
                EventIndicatorType.OPTION_TYPE, value_list=[SIDE_VALUES[args.side]]
            )
        )

    if with_ticker_type and action != "ALL":
        filters.append(
            OptionEventFilter(
                EventIndicatorType.TICKER_TYPE, value_list=[ACTION_VALUES[action]]
            )
        )

    owners = parse_owners(args.owners)
    if owners:
        filters.append(
            OptionEventFilter(EventIndicatorType.OWNER_LIST, security_list=owners)
        )

    # Live dumps can push min-turnover server-side. Backtest and cluster-only
    # keep small prints so they can still join a cluster.
    if (
        not args.backtest
        and not getattr(args, "cluster_only", False)
        and args.min_turnover
        and args.min_turnover > 0
    ):
        filters.append(
            OptionEventFilter(
                EventIndicatorType.TURNOVER, interval_min=float(args.min_turnover)
            )
        )

    if args.max_day_num is not None:
        filters.append(
            OptionEventFilter(
                EventIndicatorType.MAX_DAY_NUM, value_list=[int(args.max_day_num)]
            )
        )

    return filters


def fetch_events(quote_ctx, args, with_ticker_type=True, action=None):
    """
    Fetch up to `--pages` pages of HK_SECURITY option events.

    Returns (ret_code, message, list_of_dataframes, all_count).
    """
    filters = build_filters(args, with_ticker_type=with_ticker_type, action=action)
    sort = OptionEventSort(EventIndicatorType.TIME, EventSortDir.DESCEND)

    frames = []
    page = ""
    all_count = None
    for _ in range(max(1, args.pages)):
        ret, data = quote_ctx.get_option_event(
            OptionMarket.HK_SECURITY,
            count=args.count,
            page=page or None,
            filter_list=filters or None,
            sort=sort,
        )
        if ret != RET_OK:
            return ret, data, frames, all_count

        frames.append(data["event_list"])
        all_count = data.get("all_count")
        page = data.get("next_page") or ""
        if not page:
            break

    return RET_OK, "", frames, all_count


def expiry_from_option_code(code):
    """Parse YYMMDD from codes like HK.MNX260918C235000 / MNX260918C235."""
    m = OPTION_YYMMDD_RE.search(str(code) if code is not None else "")
    if not m:
        return pd.NaT
    return pd.to_datetime(m.group(1), format="%y%m%d", errors="coerce")


def compute_dte_at_fill(fill_dt, strike_dt, option_code):
    """Calendar days from fill date to expiry. Do not use OpenD `dte`."""
    expiry = strike_dt
    if pd.isna(expiry):
        expiry = expiry_from_option_code(option_code)
    if pd.isna(fill_dt) or pd.isna(expiry):
        return pd.NA
    fill_day = pd.Timestamp(fill_dt).normalize()
    expiry_day = pd.Timestamp(expiry).normalize()
    return int((expiry_day - fill_day).days)


def expiry_week_key(expiry):
    if pd.isna(expiry):
        return ""
    ts = pd.Timestamp(expiry)
    return f"{ts.isocalendar().year}-W{ts.isocalendar().week:02d}"


def concat_frames(frames):
    nonempty = [f for f in frames if f is not None and not f.empty]
    if not nonempty:
        return pd.DataFrame()
    return pd.concat(nonempty, ignore_index=True)


def enrich(raw):
    """Add computed DTE / dates. Never trust OpenD `dte` for replay."""
    if raw is None or raw.empty:
        return pd.DataFrame()

    out = raw.copy()
    out["fill_dt"] = pd.to_datetime(out.get("fill_time"), errors="coerce")
    out["expiry_dt"] = pd.to_datetime(out.get("strike_time"), errors="coerce")
    missing_expiry = out["expiry_dt"].isna()
    if missing_expiry.any():
        out.loc[missing_expiry, "expiry_dt"] = out.loc[
            missing_expiry, "option_code"
        ].map(expiry_from_option_code)

    out["fill_date"] = out["fill_dt"].dt.date
    out["expiry_date"] = out["expiry_dt"].dt.date
    out["dte_at_fill"] = [
        compute_dte_at_fill(f, e, c)
        for f, e, c in zip(out["fill_dt"], out["expiry_dt"], out["option_code"])
    ]
    out["dte_at_fill"] = pd.to_numeric(out["dte_at_fill"], errors="coerce")
    out["expiry_week"] = out["expiry_dt"].map(expiry_week_key)
    out["volume"] = pd.to_numeric(out.get("volume"), errors="coerce")
    out["turnover"] = pd.to_numeric(out.get("turnover"), errors="coerce")
    out["price"] = pd.to_numeric(out.get("price"), errors="coerce")
    out["strike_price"] = pd.to_numeric(out.get("strike_price"), errors="coerce")
    out["underlying_price"] = pd.to_numeric(
        out.get("underlying_price"), errors="coerce"
    )
    return out


def apply_client_filters(df, args, since=None, until=None, action=None):
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()

    action = args.action if action is None else action
    out = df
    if args.side != "ALL":
        out = out[out["option_type"] == args.side]
    if action != "ALL":
        out = out[out["ticker_type"] == action]
    if args.min_volume:
        out = out[out["volume"].fillna(0) >= args.min_volume]
    if args.max_dte is not None:
        out = out[
            out["dte_at_fill"].notna()
            & (out["dte_at_fill"] >= 0)
            & (out["dte_at_fill"] <= args.max_dte)
        ]
    if since is not None:
        out = out[out["fill_date"] >= since]
    if until is not None:
        out = out[out["fill_date"] <= until]
    return out.reset_index(drop=True)


def opposite_action(action):
    if action == "BUY":
        return "SELL"
    if action == "SELL":
        return "BUY"
    return None


def fetch_near_for_action(quote_ctx, args, action, since, until):
    """Fetch + enrich + client-filter near-expiry prints for one aggressor.

    Returns (near_df, note). near_df is None only on a hard OpenD failure.
    """
    ret, msg, frames, _ = fetch_events(
        quote_ctx, args, with_ticker_type=True, action=action
    )
    if ret != RET_OK and action != "ALL":
        ret, msg, frames, _ = fetch_events(
            quote_ctx, args, with_ticker_type=False, action=action
        )
    if ret != RET_OK:
        return None, f"get_option_event({action}) failed: {msg}"
    enriched = enrich(concat_frames(frames))
    near = apply_client_filters(
        enriched, args, since=since, until=until, action=action
    )
    return near, ""


def apply_imbalance_gate(quote_ctx, args, near, since, until):
    """Owner-day near-expiry buy/sell imbalance gate.

    Keep an owner+fill_date only when near-expiry same-side BUY turnover is
    >= min_buy_sell_ratio * near-expiry same-side SELL turnover. A day with
    zero SELL turnover always passes. Returns (kept_df, kept_days, dropped_days).
    Filtering whole owner-days before clustering matches the study harness.
    """
    ratio = args.min_buy_sell_ratio
    if near is None or near.empty or ratio is None or float(ratio) <= 0:
        return near, 0, 0
    opp = opposite_action(args.action)
    if opp is None:
        return near, 0, 0

    sell_near, note = fetch_near_for_action(quote_ctx, args, opp, since, until)
    if sell_near is None:
        print(f"WARNING: imbalance gate skipped ({note}).", file=sys.stderr)
        return near, 0, 0

    buy_day = near.groupby(["owner_code", "fill_date"], dropna=False)[
        "turnover"
    ].sum()
    sell_day = (
        sell_near.groupby(["owner_code", "fill_date"], dropna=False)[
            "turnover"
        ].sum()
        if sell_near is not None and not sell_near.empty
        else pd.Series(dtype=float)
    )
    keep = set()
    for key, bto in buy_day.items():
        sto = float(sell_day.get(key, 0.0))
        if sto <= 0 or float(bto) >= float(ratio) * sto:
            keep.add(key)
    mask = near.apply(
        lambda r: (r["owner_code"], r["fill_date"]) in keep, axis=1
    )
    kept = near[mask].reset_index(drop=True)
    return kept, len(keep), len(buy_day) - len(keep)


def display_frame(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=DISPLAY_COLUMNS)
    return pd.DataFrame(
        {
            "time": df.get("fill_time", df.get("time")),
            "owner": df.get("owner_code", df.get("owner")),
            "option_code": df.get("option_code"),
            "ticker_type": df.get("ticker_type"),
            "option_type": df.get("option_type"),
            "strike": df.get("strike_price", df.get("strike")),
            "expiry": df.get("strike_time", df.get("expiry")),
            "dte_at_fill": df.get("dte_at_fill"),
            "price": df.get("price"),
            "volume": df.get("volume"),
            "turnover": df.get("turnover"),
            "underlying": df.get("underlying_price", df.get("underlying")),
            "sentiment": df.get("sentiment"),
        }
    )


def stringify_lists(series):
    return series.apply(
        lambda v: ",".join(v) if isinstance(v, list) else ("" if v is None else str(v))
    )


def save_csv(df, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    export = df.copy()
    for col in export.columns:
        if export[col].dtype == object:
            sample = next((v for v in export[col] if v is not None), None)
            if isinstance(sample, list):
                export[col] = stringify_lists(export[col])
    export.to_csv(path, index=False)
    return str(path)


def print_table(df, empty_msg="No matching prints found in the fetched window."):
    if df is None or df.empty:
        print(empty_msg)
        return
    show = df.copy()
    with pd.option_context(
        "display.max_rows",
        None,
        "display.max_columns",
        None,
        "display.width",
        260,
        "display.max_colwidth",
        40,
        "display.float_format",
        lambda x: f"{x:,.2f}" if abs(x) >= 100 else f"{x:.4g}",
    ):
        print(show.to_string(index=False))


def find_clusters(df, window_min, min_prints, min_turnover, same_strike=False):
    """
    Cluster near-expiry prints by owner + expiry ISO week + calendar day.

    A cluster is a greedy left-to-right burst whose span is <= window_min.
    It qualifies if print count >= min_prints OR summed turnover >= min_turnover.
    same_strike=True further splits groups by strike_price.
    """
    empty_cols = [
        "cluster_id",
        "owner_code",
        "fill_date",
        "expiry_week",
        "n_prints",
        "turnover_sum",
        "t_first",
        "t_last",
        "row_indices",
        "qualifies",
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=empty_cols), {}

    window = timedelta(minutes=float(window_min))
    records = []
    membership = {}  # source index -> cluster record

    group_keys = ["owner_code", "expiry_week", "fill_date"]
    if same_strike:
        group_keys = group_keys + ["strike_price"]
    grouped = df.groupby(group_keys, dropna=False)
    for key, g in grouped:
        if same_strike:
            owner, week, day, strike = key
        else:
            owner, week, day = key
            strike = None
        g = g.sort_values("fill_dt", kind="mergesort")
        idxs = list(g.index)
        i = 0
        seq = 0
        while i < len(idxs):
            start_i = i
            start_t = g.loc[idxs[i], "fill_dt"]
            j = i
            while j + 1 < len(idxs):
                nxt_t = g.loc[idxs[j + 1], "fill_dt"]
                if pd.isna(start_t) or pd.isna(nxt_t) or (nxt_t - start_t) > window:
                    break
                j += 1
            members = idxs[start_i : j + 1]
            n_prints = len(members)
            turnover_sum = float(g.loc[members, "turnover"].fillna(0).sum())
            t_first = g.loc[members[0], "fill_dt"]
            t_last = g.loc[members[-1], "fill_dt"]
            seq += 1
            if same_strike and strike is not None:
                cid = f"{owner}|{week}|{day}|{strike}|{seq}"
            else:
                cid = f"{owner}|{week}|{day}|{seq}"
            qualifies = (n_prints >= min_prints) or (turnover_sum >= min_turnover)
            rec = {
                "cluster_id": cid,
                "owner_code": owner,
                "fill_date": day,
                "expiry_week": week,
                "n_prints": n_prints,
                "turnover_sum": turnover_sum,
                "t_first": t_first,
                "t_last": t_last,
                "row_indices": members,
                "qualifies": qualifies,
            }
            records.append(rec)
            if qualifies:
                for src_idx in members:
                    membership[src_idx] = rec
            i = j + 1

    return pd.DataFrame(records), membership


def attach_alerts(df, membership, min_turnover):
    if df is None or df.empty:
        return pd.DataFrame()

    rows = []
    for idx, row in df.iterrows():
        rec = membership.get(idx)
        size_hit = float(row.get("turnover") or 0) >= float(min_turnover)
        cluster_hit = rec is not None
        if not size_hit and not cluster_hit:
            continue
        if size_hit and cluster_hit:
            reason = "size+cluster"
        elif cluster_hit:
            reason = "cluster"
        else:
            reason = "size"
        item = row.copy()
        item["alert_reason"] = reason
        item["cluster_id"] = rec["cluster_id"] if rec else ""
        item["cluster_prints"] = rec["n_prints"] if rec else pd.NA
        item["cluster_turnover"] = rec["turnover_sum"] if rec else pd.NA
        rows.append(item)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("fill_dt", kind="mergesort").reset_index(drop=True)


def daily_rollup(df, top_n=8):
    if df is None or df.empty:
        return pd.DataFrame()
    g = (
        df.groupby(["fill_date", "owner_code"], dropna=False)
        .agg(
            n_prints=("option_code", "size"),
            turnover=("turnover", "sum"),
            volume=("volume", "sum"),
            first_time=("fill_dt", "min"),
            last_time=("fill_dt", "max"),
        )
        .reset_index()
        .sort_values(["fill_date", "turnover"], ascending=[True, False])
    )
    # Keep top_n owners per day for the printed rollup; CSV gets the full table.
    g["rank"] = g.groupby("fill_date")["turnover"].rank(method="first", ascending=False)
    return g


def fetch_prior_closes(quote_ctx, owners, start, end):
    """Map (owner, date) -> prior close via daily K-line. Failures are skipped."""
    closes = {}
    if not owners or start is None or end is None:
        return closes
    start_s = (start - timedelta(days=10)).isoformat()
    end_s = end.isoformat()
    for owner in owners:
        try:
            ret, kline, _ = quote_ctx.request_history_kline(
                owner,
                start=start_s,
                end=end_s,
                ktype=KLType.K_DAY,
                max_count=30,
            )
        except Exception:
            continue
        if ret != RET_OK or kline is None or kline.empty:
            continue
        kline = kline.copy()
        kline["day"] = pd.to_datetime(kline["time_key"], errors="coerce").dt.date
        for _, row in kline.iterrows():
            prev = row.get("last_close")
            if prev is None or (isinstance(prev, float) and pd.isna(prev)):
                continue
            closes[(owner, row["day"])] = float(prev)
    return closes


def apply_underlying_chg(alerts, prior_closes, min_pct=None, max_pct=None):
    """Drop alerts outside [min_pct, max_pct] underlying change at fill.

    Change is vs prior daily close, so it is known at fill (no look-ahead).
    Either bound may be None. Returns (kept_df, skipped_df).
    """
    if (
        alerts is None
        or alerts.empty
        or (min_pct is None and max_pct is None)
    ):
        return alerts, pd.DataFrame()
    kept = []
    skipped = []
    for _, row in alerts.iterrows():
        key = (row.get("owner_code"), row.get("fill_date"))
        prev = prior_closes.get(key)
        under = row.get("underlying_price")
        if prev and prev > 0 and under is not None and not pd.isna(under):
            chg = (float(under) / prev - 1.0) * 100.0
            row = row.copy()
            row["underlying_chg_pct"] = chg
            if min_pct is not None and chg < float(min_pct):
                skipped.append(row)
                continue
            if max_pct is not None and chg > float(max_pct):
                skipped.append(row)
                continue
        kept.append(row)
    kept_df = pd.DataFrame(kept) if kept else pd.DataFrame()
    skip_df = pd.DataFrame(skipped) if skipped else pd.DataFrame()
    return kept_df, skip_df


def fmt_hkd(v):
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return str(v)


def fmt_ts(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


def print_cluster_table(clusters):
    q = clusters[clusters["qualifies"]].copy() if not clusters.empty else clusters
    if q is None or q.empty:
        print("No qualifying clusters.")
        return
    show = pd.DataFrame(
        {
            "cluster_id": q["cluster_id"],
            "owner": q["owner_code"],
            "day": q["fill_date"].astype(str),
            "expiry_week": q["expiry_week"],
            "prints": q["n_prints"],
            "turnover": q["turnover_sum"],
            "first": q["t_first"].map(fmt_ts),
            "last": q["t_last"].map(fmt_ts),
        }
    )
    print_table(show, empty_msg="No qualifying clusters.")


def print_daily_rollup(rollup, top_n=8):
    if rollup is None or rollup.empty:
        print("No near-expiry prints to roll up.")
        return
    top = rollup[rollup["rank"] <= top_n].copy()
    for day, g in top.groupby("fill_date", sort=True):
        print(f"\n  {day}")
        show = pd.DataFrame(
            {
                "owner": g["owner_code"],
                "prints": g["n_prints"],
                "turnover": g["turnover"],
                "volume": g["volume"],
                "first": g["first_time"].map(fmt_ts),
                "last": g["last_time"].map(fmt_ts),
            }
        )
        print_table(show)


def print_minimax_check(raw_enriched, near, alerts, clusters):
    """Dedicated last-week MiniMax / HK.00100 section for the known 9/16 lead."""
    print("\n" + "=" * 78)
    print(
        f"FOCUS CHECK  {FOCUS_OWNER} / {FOCUS_NAME}  "
        f"{FOCUS_FROM.isoformat()} .. {FOCUS_TO.isoformat()}"
    )
    print("=" * 78)

    src = raw_enriched if raw_enriched is not None else pd.DataFrame()
    if src.empty or "owner_code" not in src.columns:
        print("No events in the fetched window — cannot check MiniMax.")
        return

    mm = src[src["owner_code"] == FOCUS_OWNER].copy()
    mm_week = mm[
        (mm["fill_date"] >= FOCUS_FROM) & (mm["fill_date"] <= FOCUS_TO)
    ]
    print(f"Fetched {FOCUS_OWNER} events in window: {len(mm_week)} (all sides/DTE)")
    if mm_week.empty:
        print("MiniMax is absent from this OpenD page — widen --max-day-num / --pages.")
        return

    near_mm = pd.DataFrame()
    if near is not None and not near.empty and "owner_code" in near.columns:
        near_mm = near[near["owner_code"] == FOCUS_OWNER]
        near_mm = near_mm[
            (near_mm["fill_date"] >= FOCUS_FROM) & (near_mm["fill_date"] <= FOCUS_TO)
        ]
    print(f"Near-expiry matching-side prints on {FOCUS_OWNER}: {len(near_mm)}")

    needle = mm_week[
        mm_week["option_code"].astype(str).str.contains(FOCUS_CONTRACT_NEEDLE, na=False)
    ].sort_values("fill_dt", kind="mergesort")
    print(
        f"\nContract {FOCUS_CONTRACT_NEEDLE} (MNX260918C235 / HK.MNX260918C235000): "
        f"{len(needle)} print(s)"
    )
    if not needle.empty:
        show = needle[
            [
                "fill_time",
                "option_code",
                "ticker_type",
                "dte_at_fill",
                "price",
                "volume",
                "turnover",
                "underlying_price",
            ]
        ].rename(columns={"fill_time": "time", "underlying_price": "underlying"})
        print_table(show)

    morning = needle[
        (needle["fill_date"] == FOCUS_MORNING) & (needle["ticker_type"] == "BUY")
    ]
    print(
        f"\n{FOCUS_MORNING.isoformat()} morning CALL BUY on {FOCUS_CONTRACT_NEEDLE}: "
        f"{len(morning)} print(s)"
    )
    if not morning.empty:
        to_sum = float(morning["turnover"].fillna(0).sum())
        t0 = morning["fill_dt"].min()
        t1 = morning["fill_dt"].max()
        span_min = (t1 - t0).total_seconds() / 60.0 if pd.notna(t0) and pd.notna(t1) else 0
        print(
            f"  times {fmt_ts(t0)} -> {fmt_ts(t1)}  ({span_min:.1f} min)  "
            f"turnover {fmt_hkd(to_sum)} HKD  volume {int(morning['volume'].fillna(0).sum())}"
        )

    mm_alerts = (
        alerts[
            (alerts["owner_code"] == FOCUS_OWNER)
            & (alerts["fill_date"] >= FOCUS_FROM)
            & (alerts["fill_date"] <= FOCUS_TO)
        ]
        if alerts is not None and not alerts.empty
        else pd.DataFrame()
    )
    mm_morning_alerts = (
        mm_alerts[mm_alerts["fill_date"] == FOCUS_MORNING]
        if not mm_alerts.empty
        else mm_alerts
    )
    fired = not mm_morning_alerts.empty
    print(
        f"\nWould an alert fire on {FOCUS_MORNING.isoformat()} morning "
        f"for near-expiry CALL buys on {FOCUS_CONTRACT_NEEDLE}?"
    )
    if fired:
        reasons = sorted(mm_morning_alerts["alert_reason"].dropna().unique().tolist())
        n = len(mm_morning_alerts)
        to_sum = float(mm_morning_alerts["turnover"].fillna(0).sum())
        print(
            f"  YES — {n} alert print(s), reasons={reasons}, "
            f"turnover {fmt_hkd(to_sum)} HKD"
        )
        cids = [c for c in mm_morning_alerts["cluster_id"].dropna().unique() if c]
        if cids and clusters is not None and not clusters.empty:
            for cid in cids:
                hit = clusters[clusters["cluster_id"] == cid]
                if hit.empty:
                    continue
                r = hit.iloc[0]
                print(
                    f"  cluster {cid}: {int(r['n_prints'])} prints, "
                    f"{fmt_hkd(r['turnover_sum'])} HKD, "
                    f"{fmt_ts(r['t_first'])} -> {fmt_ts(r['t_last'])}"
                )
    else:
        print("  NO — no size or cluster alert on that morning in this replay.")
        if not morning.empty:
            print(
                "  (Prints exist; they missed min-turnover / cluster / max-dte gates.)"
            )


def other_alert_owners(alerts):
    if alerts is None or alerts.empty:
        return pd.DataFrame()
    week = alerts[
        (alerts["fill_date"] >= FOCUS_FROM) & (alerts["fill_date"] <= FOCUS_TO)
    ]
    if week.empty:
        week = alerts
    g = (
        week.groupby("owner_code")
        .agg(
            n_alerts=("option_code", "size"),
            turnover=("turnover", "sum"),
            days=("fill_date", lambda s: ",".join(sorted({str(x) for x in s}))),
            reasons=("alert_reason", lambda s: ",".join(sorted(set(s)))),
        )
        .reset_index()
        .sort_values("turnover", ascending=False)
    )
    return g


def fetch_focus_kline(quote_ctx):
    try:
        ret, kline, _ = quote_ctx.request_history_kline(
            FOCUS_OWNER,
            start="2026-09-11",
            end="2026-09-18",
            ktype=KLType.K_DAY,
            max_count=20,
        )
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    if ret != RET_OK:
        return None, str(kline)
    return kline, ""


def print_focus_kline(kline):
    print(f"\n{FOCUS_OWNER} / {FOCUS_NAME} daily bars (context for 9/17–18 surge):")
    if kline is None or kline.empty:
        print("  (kline unavailable)")
        return
    cols = [
        c
        for c in ["time_key", "open", "high", "low", "close", "last_close", "change_rate", "volume"]
        if c in kline.columns
    ]
    print_table(kline[cols])


# ---------------------------------------------------------------------------
# Rank source (get_option_rank) — daily near-expiry signals
# ---------------------------------------------------------------------------


class RateLimiter:
    """Stay under OpenD ~60 calls / 30s."""

    def __init__(self, max_calls=50, window_s=30.0, min_interval=0.55):
        self.max_calls = int(max_calls)
        self.window_s = float(window_s)
        self.min_interval = float(min_interval)
        self.times = []
        self.last = 0.0

    def wait(self):
        now = time.monotonic()
        if self.last:
            gap = now - self.last
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
                now = time.monotonic()
        cutoff = now - self.window_s
        self.times = [t for t in self.times if t > cutoff]
        if len(self.times) >= self.max_calls:
            sleep_for = self.times[0] + self.window_s - now + 0.05
            if sleep_for > 0:
                time.sleep(sleep_for)
                now = time.monotonic()
                cutoff = now - self.window_s
                self.times = [t for t in self.times if t > cutoff]
        self.times.append(now)
        self.last = now


def _is_rate_limit_msg(msg):
    text = str(msg).lower()
    needles = (
        "freq",
        "limit",
        "too many",
        "too frequent",
        "频率",
        "限制",
        "timeout",
        "超时",
        "rate",
    )
    return any(n in text for n in needles)


def call_with_retry(limiter, fn, *args, retries=6, **kwargs):
    last = None
    for attempt in range(max(1, retries)):
        limiter.wait()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(12.0, 1.5 * (attempt + 1)))
            continue
        ret = result[0] if isinstance(result, tuple) else result
        if ret == RET_OK:
            return result
        msg = result[1] if isinstance(result, tuple) and len(result) > 1 else result
        last = msg
        if _is_rate_limit_msg(msg):
            time.sleep(min(15.0, 2.0 * (attempt + 1)))
            continue
        return result
    if isinstance(last, Exception):
        raise last
    return last if isinstance(last, tuple) else (RET_ERROR, last)


def chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def parse_option_code(code):
    """Parse (root, expiry_date, CP, strike) from HK.MNX260918C235000."""
    raw = str(code).strip() if code is not None else ""
    m = OPTION_CODE_RE.match(raw.replace(" ", ""))
    if not m:
        return None, pd.NaT, "", pd.NA
    root, ymd, cp, strike_raw = m.groups()
    try:
        expiry = datetime.strptime(ymd, "%y%m%d").date()
    except ValueError:
        expiry = pd.NaT
    try:
        strike = int(strike_raw) / 1000.0
    except (TypeError, ValueError):
        strike = pd.NA
    return root.upper(), expiry, cp.upper(), strike


def normalize_trading_date(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NAN", "NONE"}:
        return None
    return text[:10]


def build_rank_filters(args):
    filters = []
    if args.side != "ALL":
        filters.append(
            OptionRankFilter(
                OptionRankIndicatorType.OPTION_TYPE,
                value_list=[SIDE_VALUES[args.side]],
            )
        )
    owners = parse_owners(args.owners)
    if owners:
        filters.append(
            OptionRankFilter(
                OptionRankIndicatorType.OWNER_LIST, security_list=owners
            )
        )
    return filters or None


def fetch_rank_page(quote_ctx, limiter, args, trading_date, page, count):
    sort_type = RANK_TYPE_MAP.get(args.rank_type, OptionRankType.TURNOVER)
    kargs = {
        "option_market": OptionMarket.HK_SECURITY,
        "sort_type": sort_type,
        "count": int(count),
        "filter_list": build_rank_filters(args),
    }
    if trading_date:
        kargs["trading_date"] = trading_date
    if page:
        kargs["page"] = page
    return call_with_retry(limiter, quote_ctx.get_option_rank, **kargs)


def fetch_rank_day(quote_ctx, limiter, args, trading_date=None):
    """
    Page get_option_rank for one session.

    Returns (status, got_date, frame, all_count, note).
    status is RET_OK, RET_ERROR, or the string 'DATE_MISMATCH'.
    When trading_date is set, the returned trading_date column MUST match
    or the day is a silent 'latest' replay and must be skipped.
    """
    frames = []
    page = None
    got_date = None
    all_count = None
    collected = 0
    rank_top = int(args.rank_top or 400)
    n_pages = max(1, int(args.pages))
    for _ in range(n_pages):
        if collected >= rank_top:
            break
        count = min(int(args.count), rank_top - collected, 200)
        result = fetch_rank_page(
            quote_ctx, limiter, args, trading_date, page, count
        )
        ret = result[0]
        if ret != RET_OK:
            msg = result[1] if len(result) > 1 else ret
            return RET_ERROR, None, concat_frames(frames), all_count, str(msg)
        data = result[1]
        nxt = result[2] if len(result) > 2 else None
        all_count = result[3] if len(result) > 3 else all_count
        if data is None or (isinstance(data, pd.DataFrame) and data.empty):
            break
        if "trading_date" in data.columns and not data.empty:
            got_date = normalize_trading_date(data["trading_date"].iloc[0])
        if trading_date and got_date and got_date != trading_date:
            return "DATE_MISMATCH", got_date, data, all_count, got_date
        frames.append(data)
        collected += len(data)
        if not nxt:
            break
        page = nxt
    return RET_OK, got_date, concat_frames(frames), all_count, ""


def list_hk_sessions(quote_ctx, limiter, start, end):
    result = call_with_retry(
        limiter,
        quote_ctx.request_trading_days,
        market=TradeDateMarket.HK,
        start=start.isoformat(),
        end=end.isoformat(),
    )
    ret, days = result[0], result[1]
    if ret != RET_OK or not isinstance(days, list):
        out = []
        cur = start
        while cur <= end:
            if cur.weekday() < 5:
                out.append(cur)
            cur += timedelta(days=1)
        return out
    out = []
    for item in days:
        raw = item.get("time") if isinstance(item, dict) else item
        text = normalize_trading_date(raw)
        if not text:
            continue
        try:
            day = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            continue
        if start <= day <= end:
            out.append(day)
    return out


def enrich_rank(raw):
    if raw is None or raw.empty:
        return pd.DataFrame()
    out = raw.copy()
    parsed = out["code"].map(parse_option_code)
    out["root"] = [p[0] for p in parsed]
    out["expiry"] = [p[1] for p in parsed]
    out["cp"] = [p[2] for p in parsed]
    out["strike"] = [p[3] for p in parsed]
    out["trading_date"] = out["trading_date"].map(normalize_trading_date)
    out["rank_day"] = pd.to_datetime(out["trading_date"], errors="coerce").dt.date
    dte = []
    for day, expiry in zip(out["rank_day"], out["expiry"]):
        if pd.isna(day) or pd.isna(expiry):
            dte.append(pd.NA)
            continue
        exp = expiry if hasattr(expiry, "year") else pd.Timestamp(expiry).date()
        dte.append(int((exp - day).days))
    out["dte"] = pd.to_numeric(pd.Series(dte, index=out.index), errors="coerce")
    for col in (
        "volume",
        "turnover",
        "oi_increment",
        "oi_decrement",
        "open_interest",
        "iv",
        "change_ratio",
        "option_price",
        "delta",
        "gamma",
        "theta",
        "vega",
        "rho",
    ):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "option_type" in out.columns:
        out["option_type"] = out["option_type"].astype(str).str.upper()
        out.loc[out["option_type"].isin(["N/A", "NAN", "NONE", ""]), "option_type"] = (
            out["cp"].map({"C": "CALL", "P": "PUT"}).fillna(out["option_type"])
        )
    return out


def apply_rank_filters(df, args):
    if df is None or df.empty:
        return pd.DataFrame()
    out = df
    if args.side != "ALL" and "option_type" in out.columns:
        out = out[out["option_type"] == args.side]
    if args.min_volume:
        out = out[out["volume"].fillna(0) >= args.min_volume]
    if args.max_dte is not None:
        out = out[
            out["dte"].notna()
            & (out["dte"] >= 0)
            & (out["dte"] <= args.max_dte)
        ]
    return out.reset_index(drop=True)


def resolve_owners(quote_ctx, limiter, df):
    """Fill owner / owner_name from basicinfo, then static root map."""
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    out = df.copy()
    owner_by_root = dict(ROOT_OWNER_FALLBACK)
    name_by_owner = dict(OWNER_NAME_FALLBACK)

    reps = {}
    for code, root in zip(out["code"], out["root"]):
        if root and root not in reps:
            reps[root] = code
    unknown_codes = [code for root, code in reps.items() if root not in owner_by_root]
    for batch in chunks(unknown_codes, 80):
        result = call_with_retry(
            limiter,
            quote_ctx.get_stock_basicinfo,
            Market.HK,
            SecurityType.DRVT,
            batch,
        )
        ret, info = result[0], result[1]
        if ret != RET_OK or info is None or getattr(info, "empty", True):
            continue
        for _, row in info.iterrows():
            code = row.get("code")
            owner = row.get("stock_owner")
            root, _, _, _ = parse_option_code(code)
            if not root:
                continue
            if owner is not None and str(owner) not in {"", "N/A", "nan", "None"}:
                owner_by_root[root] = str(owner)

    out["owner"] = out["root"].map(lambda r: owner_by_root.get(r) or r)
    owners = sorted({o for o in out["owner"].dropna().unique() if str(o).startswith("HK.")})
    missing = [o for o in owners if o not in name_by_owner]
    for batch in chunks(missing, 80):
        result = call_with_retry(
            limiter,
            quote_ctx.get_stock_basicinfo,
            Market.HK,
            SecurityType.STOCK,
            batch,
        )
        ret, info = result[0], result[1]
        if ret != RET_OK or info is None or getattr(info, "empty", True):
            continue
        for _, row in info.iterrows():
            code = row.get("code")
            name = row.get("name")
            if code and name and str(name) not in {"", "N/A"}:
                name_by_owner[str(code)] = str(name)
    out["owner_name"] = out["owner"].map(lambda o: name_by_owner.get(o, ""))
    return out


def rank_owner_days(near):
    empty_cols = RANK_ALERT_COLUMNS
    if near is None or near.empty:
        return pd.DataFrame(columns=empty_cols)
    rows = []
    grouped = near.groupby(["trading_date", "owner"], dropna=False)
    for (day, owner), part in grouped:
        part = part.sort_values(["turnover", "volume"], ascending=[False, False])
        lead = part.iloc[0]
        rows.append(
            {
                "trading_date": day,
                "owner": owner,
                "owner_name": lead.get("owner_name") or "",
                "lead_code": lead.get("code"),
                "lead_name": lead.get("name"),
                "option_type": lead.get("option_type"),
                "strike": lead.get("strike"),
                "expiry": lead.get("expiry"),
                "dte": lead.get("dte"),
                "volume": lead.get("volume"),
                "turnover": lead.get("turnover"),
                "owner_turnover": float(part["turnover"].fillna(0).sum()),
                "owner_volume": float(part["volume"].fillna(0).sum()),
                "n_contracts": int(len(part)),
                "oi_increment": lead.get("oi_increment"),
                "max_oi_increment": float(part["oi_increment"].fillna(0).max())
                if "oi_increment" in part.columns
                else 0.0,
                "iv": lead.get("iv"),
                "change_ratio": lead.get("change_ratio"),
                "option_price": lead.get("option_price"),
                "root": lead.get("root"),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["trading_date", "owner_turnover"], ascending=[True, False]
    )


def apply_rank_signal(owner_days, args):
    if owner_days is None or owner_days.empty:
        return pd.DataFrame()
    out = owner_days
    min_to = float(args.min_turnover or 0)
    min_own = float(args.min_owner_turnover or 0)
    min_vol = float(args.min_volume or 0)
    min_oi = args.min_oi_increment
    mask = out["turnover"].fillna(0) >= min_to
    if min_vol:
        mask = mask & (out["volume"].fillna(0) >= min_vol)
    if min_own > 0:
        mask = mask & (out["owner_turnover"].fillna(0) >= min_own)
    if min_oi is not None:
        oi_col = (
            out["max_oi_increment"]
            if "max_oi_increment" in out.columns
            else out["oi_increment"]
        )
        mask = mask & (pd.to_numeric(oi_col, errors="coerce").fillna(0) >= float(min_oi))
    kept = out[mask].copy()
    if kept.empty:
        return kept

    def _reason(row):
        bits = []
        if float(row.get("turnover") or 0) >= min_to:
            bits.append("contract")
        if min_own > 0 and float(row.get("owner_turnover") or 0) >= min_own:
            bits.append("owner_sum")
        if min_oi is not None:
            bits.append("oi")
        return "+".join(bits) if bits else "rank"

    kept["alert_reason"] = kept.apply(_reason, axis=1)
    return kept.reset_index(drop=True)


def fetch_owner_klines(quote_ctx, limiter, owners, start, end):
    """Map owner -> daily kline frame with a `day` date column."""
    out = {}
    if not owners:
        return out
    start_s = (start - timedelta(days=14)).isoformat()
    end_s = (end + timedelta(days=14)).isoformat()
    for owner in owners:
        if not owner or not str(owner).startswith("HK."):
            continue
        try:
            result = call_with_retry(
                limiter,
                quote_ctx.request_history_kline,
                owner,
                start=start_s,
                end=end_s,
                ktype=KLType.K_DAY,
                max_count=80,
            )
        except Exception:
            continue
        ret = result[0]
        kline = result[1] if len(result) > 1 else None
        if ret != RET_OK or kline is None or getattr(kline, "empty", True):
            continue
        frame = kline.copy()
        frame["day"] = pd.to_datetime(frame["time_key"], errors="coerce").dt.date
        for col in ("open", "close", "last_close", "high", "low", "change_rate"):
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
        out[owner] = frame.sort_values("day").reset_index(drop=True)
    return out


def attach_stock_fwd(alerts, klines):
    """Attach ranking-day chg (vs prior close) and ret_1d / ret_2d. No look-ahead."""
    if alerts is None or alerts.empty:
        return alerts if alerts is not None else pd.DataFrame()
    out = alerts.copy()
    for col in ("day_chg_pct", "open_chg_pct", "ret_1d", "ret_2d", "close", "prior_close"):
        if col not in out.columns:
            out[col] = pd.NA
    for idx, row in out.iterrows():
        owner = row.get("owner")
        day = row.get("trading_date")
        if hasattr(day, "isoformat"):
            pass
        else:
            try:
                day = datetime.strptime(str(day)[:10], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                continue
        frame = klines.get(owner)
        if frame is None or frame.empty:
            continue
        hit = frame[frame["day"] == day]
        if hit.empty:
            continue
        bar = hit.iloc[0]
        close = bar.get("close")
        prior = bar.get("last_close")
        opn = bar.get("open")
        out.at[idx, "close"] = close
        out.at[idx, "prior_close"] = prior
        if prior and prior > 0 and close is not None and not pd.isna(close):
            out.at[idx, "day_chg_pct"] = (float(close) / float(prior) - 1.0) * 100.0
        if prior and prior > 0 and opn is not None and not pd.isna(opn):
            out.at[idx, "open_chg_pct"] = (float(opn) / float(prior) - 1.0) * 100.0
        later = frame[frame["day"] > day].sort_values("day")
        if close and close > 0 and not later.empty:
            c1 = later.iloc[0].get("close")
            if c1 is not None and not pd.isna(c1):
                out.at[idx, "ret_1d"] = (float(c1) / float(close) - 1.0) * 100.0
            if len(later) >= 2:
                c2 = later.iloc[1].get("close")
                if c2 is not None and not pd.isna(c2):
                    out.at[idx, "ret_2d"] = (float(c2) / float(close) - 1.0) * 100.0
    return out


def apply_rank_chg_gate(alerts, args):
    """Causal EOD gate: ranking-day close vs prior close. No future bars."""
    if alerts is None or alerts.empty:
        return alerts, pd.DataFrame()
    min_pct = args.min_underlying_chg
    max_pct = args.require_stock_not_up
    if min_pct is None and max_pct is None:
        return alerts, pd.DataFrame()
    kept = []
    skipped = []
    for _, row in alerts.iterrows():
        chg = row.get("day_chg_pct")
        if chg is None or (isinstance(chg, float) and pd.isna(chg)):
            # Unknown chg: keep (do not silently drop MiniMax if kline lags).
            kept.append(row)
            continue
        chg = float(chg)
        if min_pct is not None and chg < float(min_pct):
            skipped.append(row)
            continue
        if max_pct is not None and chg > float(max_pct):
            skipped.append(row)
            continue
        kept.append(row)
    kept_df = pd.DataFrame(kept) if kept else pd.DataFrame()
    skip_df = pd.DataFrame(skipped) if skipped else pd.DataFrame()
    return kept_df, skip_df


def rank_daily_rollup(near):
    if near is None or near.empty:
        return pd.DataFrame()
    ordered = near.sort_values("turnover", ascending=False)
    g = (
        ordered.groupby(["trading_date", "owner"], dropna=False, sort=False)
        .agg(
            owner_name=("owner_name", "first"),
            n_contracts=("code", "size"),
            turnover=("turnover", "sum"),
            volume=("volume", "sum"),
            lead_code=("code", "first"),
            min_dte=("dte", "min"),
            max_dte=("dte", "max"),
        )
        .reset_index()
        .sort_values(["trading_date", "turnover"], ascending=[True, False])
    )
    g["rank"] = g.groupby("trading_date")["turnover"].rank(
        method="first", ascending=False
    )
    return g


def display_rank_live(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=RANK_LIVE_COLUMNS)
    cols = [c for c in RANK_LIVE_COLUMNS if c in df.columns]
    return df[cols].copy()


def display_rank_alerts(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=RANK_ALERT_COLUMNS)
    cols = [c for c in RANK_ALERT_COLUMNS if c in df.columns]
    return df[cols].copy()


def win_rate(series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return None, 0, None
    wins = int((s > 0).sum())
    return wins / len(s), len(s), float(s.mean())


def print_fwd_stats(alerts, label=""):
    if alerts is None or alerts.empty:
        print(f"Forward returns{label}: no alerts.")
        return
    for col, name in (("ret_1d", "次日 ret_1d"), ("ret_2d", "两日 ret_2d")):
        rate, n, mean = win_rate(alerts.get(col))
        if n == 0 or rate is None:
            print(f"  {name}: 无下一根日K（样本 0）")
            continue
        print(
            f"  {name}: n={n}  胜率 {rate:.0%}  均值 {mean:+.2f}%"
        )


def print_rank_minimax(raw, near, alerts):
    print("\n" + "=" * 78)
    print(
        f"FOCUS CHECK  {FOCUS_OWNER} / {FOCUS_NAME} / {FOCUS_ROOT}  "
        f"{FOCUS_FROM.isoformat()} .. {FOCUS_TO.isoformat()}"
    )
    print("=" * 78)
    src = raw if raw is not None else pd.DataFrame()
    if src.empty:
        print("No rank rows in the fetched window — cannot check MiniMax.")
        return

    def _is_mm(frame):
        code = frame["code"].astype(str) if "code" in frame.columns else pd.Series(dtype=str)
        owner = frame["owner"].astype(str) if "owner" in frame.columns else pd.Series(dtype=str)
        root = frame["root"].astype(str) if "root" in frame.columns else pd.Series(dtype=str)
        return (
            owner.eq(FOCUS_OWNER)
            | root.eq(FOCUS_ROOT)
            | code.str.contains(FOCUS_ROOT, na=False)
        )

    mm = src[_is_mm(src)].copy()
    if "rank_day" in mm.columns:
        mm_week = mm[(mm["rank_day"] >= FOCUS_FROM) & (mm["rank_day"] <= FOCUS_TO)]
    else:
        mm_week = mm
    print(f"Fetched {FOCUS_OWNER}/{FOCUS_ROOT} rank rows in focus week: {len(mm_week)}")
    needle = (
        mm_week[
            mm_week["code"].astype(str).str.contains(FOCUS_CONTRACT_NEEDLE, na=False)
        ]
        if not mm_week.empty
        else pd.DataFrame()
    )
    print(
        f"Contract {FOCUS_CONTRACT_NEEDLE} (MNX260918C235 / HK.MNX260918C235000): "
        f"{len(needle)} rank row(s)"
    )
    if not needle.empty:
        cols = [
            c
            for c in [
                "trading_date",
                "code",
                "option_type",
                "dte",
                "volume",
                "turnover",
                "oi_increment",
            ]
            if c in needle.columns
        ]
        print_table(needle[cols])

    day16 = (
        needle[needle["trading_date"].astype(str).str.startswith("2026-09-16")]
        if not needle.empty
        else pd.DataFrame()
    )
    if day16.empty and not mm_week.empty:
        day16 = mm_week[mm_week["trading_date"].astype(str).str.startswith("2026-09-16")]
        day16 = day16[
            day16["code"].astype(str).str.contains(FOCUS_CONTRACT_NEEDLE, na=False)
        ]

    mm_alerts = pd.DataFrame()
    if alerts is not None and not alerts.empty:
        a = alerts
        code_col = "lead_code" if "lead_code" in a.columns else "code"
        owner_ok = a["owner"].astype(str).eq(FOCUS_OWNER) if "owner" in a.columns else False
        code_ok = a[code_col].astype(str).str.contains(FOCUS_ROOT, na=False)
        mm_alerts = a[owner_ok | code_ok]
        mm_alerts = mm_alerts[
            mm_alerts["trading_date"].astype(str).str.startswith("2026-09-16")
        ]
    fired = not mm_alerts.empty
    print(
        f"\nWould a RANK alert fire on {FOCUS_MORNING.isoformat()} "
        f"for near-expiry CALL {FOCUS_CONTRACT_NEEDLE}?"
    )
    if fired:
        row = mm_alerts.iloc[0]
        print(
            f"  YES — owner={row.get('owner')} lead={row.get('lead_code')} "
            f"DTE={row.get('dte')} turnover={fmt_hkd(row.get('turnover'))} "
            f"owner_turnover={fmt_hkd(row.get('owner_turnover'))} "
            f"reason={row.get('alert_reason')} "
            f"day_chg={row.get('day_chg_pct')} "
            f"ret_1d={row.get('ret_1d')} ret_2d={row.get('ret_2d')}"
        )
    else:
        print("  NO — MiniMax 9/16 did not pass the current rank gates.")
        if not day16.empty:
            r = day16.iloc[0]
            print(
                f"  (Rank row exists: {r.get('code')} turnover={fmt_hkd(r.get('turnover'))} "
                f"DTE={r.get('dte')}; missed min-turnover / owner-sum / DTE / chg gate.)"
            )


def fetch_rank_window(quote_ctx, limiter, args, start, end, trading_date_required=True):
    """Fetch + validate rank pages over [start, end]. Skip date mismatches."""
    if start is None or end is None:
        status, got, frame, allc, note = fetch_rank_day(
            quote_ctx, limiter, args, trading_date=None
        )
        got_day = None
        if got:
            try:
                got_day = datetime.strptime(got, "%Y-%m-%d").date()
            except ValueError:
                got_day = None
        valid = [got_day] if got_day else []
        skipped = []
        if status != RET_OK:
            skipped.append((None, f"{status}:{note}"))
        return frame, valid, skipped, allc

    sessions = list_hk_sessions(quote_ctx, limiter, start, end)
    frames = []
    valid = []
    skipped = []
    last_allc = None
    print(f"Candidate HK sessions in range: {len(sessions)}")
    for i, day in enumerate(sessions, 1):
        iso = day.isoformat()
        status, got, frame, allc, note = fetch_rank_day(
            quote_ctx, limiter, args, trading_date=iso
        )
        last_allc = allc if allc is not None else last_allc
        if status == "DATE_MISMATCH":
            skipped.append((day, f"mismatch->{got}"))
            print(f"  skip {iso}: OpenD returned trading_date={got} (silent latest)")
            continue
        if status != RET_OK:
            skipped.append((day, note or str(status)))
            print(f"  skip {iso}: {note or status}")
            continue
        if frame is None or frame.empty:
            skipped.append((day, "empty"))
            print(f"  skip {iso}: empty rank page")
            continue
        valid.append(day)
        frames.append(frame)
        n = 0 if frame is None or frame.empty else len(frame)
        print(f"  {iso}: {n} rank row(s)  all_count={allc}  ({i}/{len(sessions)})")
    return concat_frames(frames), valid, skipped, last_allc


def run_live_rank(quote_ctx, args, start, end):
    limiter = RateLimiter(min_interval=max(0.2, float(args.sleep)))
    trading_date = None
    if end and start and start == end:
        trading_date = end.isoformat()
    elif end:
        trading_date = end.isoformat()
    elif start:
        trading_date = start.isoformat()

    print(
        f"Scanning HK listed equity options | source=rank market=HK_SECURITY "
        f"preset={args.preset or '(none)'} side={args.side} "
        f"rank_type={args.rank_type} min_turnover={args.min_turnover:,.0f} "
        f"min_owner_turnover={args.min_owner_turnover:,.0f} "
        f"max_dte={args.max_dte} rank_top={args.rank_top} "
        f"count={args.count} pages={args.pages} "
        f"trading_date={trading_date or '(latest)'}"
    )

    status, got, raw, allc, note = fetch_rank_day(
        quote_ctx, limiter, args, trading_date=trading_date
    )
    if status == "DATE_MISMATCH":
        print(
            f"ERROR: requested trading_date={trading_date} but OpenD returned "
            f"{got}. Day skipped (non-trading or beyond history).",
            file=sys.stderr,
        )
        return 1
    if status != RET_OK:
        print(f"ERROR: get_option_rank failed: {note}", file=sys.stderr)
        return 1

    enriched = enrich_rank(raw)
    enriched = resolve_owners(quote_ctx, limiter, enriched)
    near = apply_rank_filters(enriched, args)
    show = display_rank_live(near)
    if not show.empty:
        show = show.sort_values("turnover", ascending=False, ignore_index=True)
    print_table(show, empty_msg="No matching near-expiry rank rows.")

    owner_days = rank_owner_days(near)
    alerts = apply_rank_signal(owner_days, args)
    print(
        f"\nSession trading_date={got}  raw={0 if raw is None or raw.empty else len(raw)}"
        f"  near-expiry={len(near)}  owner-day alerts={len(alerts)}"
        + (f"  all_count={allc}" if allc is not None else "")
    )
    if not alerts.empty:
        print("\n--- Owner-day alerts (same gates as --backtest) ---")
        print_table(display_rank_alerts(alerts))

    out_dir = Path(args.out_dir) if args.out_dir else SCRIPT_DIR
    csv_path = args.csv or str(out_dir / LIVE_CSV_NAME)
    save_csv(show, csv_path)
    print(f"\nSaved live near-expiry rank table -> {csv_path}")
    return 0


def run_backtest_rank(quote_ctx, args, start, end):
    limiter = RateLimiter(min_interval=max(0.2, float(args.sleep)))
    if start is None:
        start = RANK_HIST_START
    if end is None:
        # Probe latest session date (no trading_date arg).
        status, got, _, _, note = fetch_rank_day(
            quote_ctx, limiter, args, trading_date=None
        )
        if status == RET_OK and got:
            try:
                end = datetime.strptime(got, "%Y-%m-%d").date()
            except ValueError:
                end = date.today()
        else:
            end = date.today()
            print(f"NOTE: latest rank date probe failed ({note}); using {end}")

    print(
        f"BACKTEST HK option rank | source=rank market=HK_SECURITY "
        f"preset={args.preset or '(none)'} side={args.side} "
        f"rank_type={args.rank_type} min_turnover={args.min_turnover:,.0f} "
        f"min_owner_turnover={args.min_owner_turnover:,.0f} "
        f"min_volume={args.min_volume} min_oi_increment={args.min_oi_increment} "
        f"max_dte={args.max_dte} rank_top={args.rank_top} "
        f"count={args.count} pages={args.pages} "
        f"from={start.isoformat()} to={end.isoformat()} "
        f"min_underlying_chg={args.min_underlying_chg}"
    )
    print(
        "Alert rule: matching side, computed DTE in [0, max_dte] vs ranking day, "
        "lead contract turnover >= min_turnover, and owner-day near-expiry "
        "turnover sum >= min_owner_turnover (if > 0)."
    )
    print(
        "DTE is computed from option-code YYMMDD vs ranking day. "
        "OpenD LEFT_DAYS / dte is ignored (those are vs today, not trading_date)."
    )
    print(
        "Returned trading_date is validated per day; mismatches (silent latest) are skipped."
    )

    raw, valid_days, skipped, allc = fetch_rank_window(
        quote_ctx, limiter, args, start, end, trading_date_required=True
    )
    if not valid_days:
        print("ERROR: no validated rank days in range.", file=sys.stderr)
        return 1

    enriched = enrich_rank(raw)
    enriched = resolve_owners(quote_ctx, limiter, enriched)
    near = apply_rank_filters(enriched, args)
    owner_days = rank_owner_days(near)
    alerts = apply_rank_signal(owner_days, args)

    owners = []
    if alerts is not None and not alerts.empty:
        owners = sorted(
            {o for o in alerts["owner"].dropna().unique() if str(o).startswith("HK.")}
        )
    klines = fetch_owner_klines(
        quote_ctx, limiter, owners, valid_days[0], valid_days[-1]
    )
    if alerts is not None and not alerts.empty:
        alerts = attach_stock_fwd(alerts, klines)
        alerts, skipped_chg = apply_rank_chg_gate(alerts, args)
        if args.min_underlying_chg is not None or args.require_stock_not_up is not None:
            print(
                f"underlying day-chg gate (min={args.min_underlying_chg}, "
                f"max={args.require_stock_not_up}): kept {len(alerts)}, "
                f"skipped {0 if skipped_chg is None else len(skipped_chg)}"
            )

    n_raw = 0 if enriched is None or enriched.empty else len(enriched)
    print(
        f"\nValidated sessions: {len(valid_days)}  "
        f"first={valid_days[0].isoformat()} last={valid_days[-1].isoformat()}"
    )
    if skipped:
        print(f"Skipped (mismatch/error/empty): {len(skipped)}")
    print(f"Raw rank rows: {n_raw}")
    print(f"Near-expiry matching-side rows: {len(near)}")
    print(f"Owner-days (near-expiry): {0 if owner_days is None or owner_days.empty else len(owner_days)}")
    print(f"Owner-day alerts: {0 if alerts is None or alerts.empty else len(alerts)}")

    print("\n--- Owner-day alerts ---")
    alert_show = display_rank_alerts(alerts)
    print_table(alert_show, empty_msg="No rank alerts under the current rules.")

    print("\n--- Forward returns (next HK sessions, close-to-close) ---")
    print_fwd_stats(alerts)

    rollup = rank_daily_rollup(near)
    print("\n--- Daily rollup (near-expiry matching side, top owners by turnover) ---")
    if rollup is None or rollup.empty:
        print("No near-expiry rank rows to roll up.")
    else:
        top = rollup[rollup["rank"] <= 8].copy()
        for day, g in top.groupby("trading_date", sort=True):
            print(f"\n  {day}")
            show = pd.DataFrame(
                {
                    "owner": g["owner"],
                    "name": g["owner_name"],
                    "n": g["n_contracts"],
                    "turnover": g["turnover"],
                    "volume": g["volume"],
                    "lead": g["lead_code"],
                }
            )
            print_table(show)

    print_rank_minimax(enriched, near, alerts)

    kline, kerr = fetch_focus_kline(quote_ctx)
    if kerr:
        print(f"\nNOTE: {FOCUS_OWNER} kline fetch failed: {kerr}")
    else:
        print_focus_kline(kline)

    out_dir = Path(args.out_dir) if args.out_dir else SCRIPT_DIR
    raw_path = out_dir / RANK_RAW_CSV_NAME
    alerts_path = out_dir / ALERTS_CSV_NAME
    rollup_path = out_dir / ROLLUP_CSV_NAME

    save_csv(enriched, raw_path)
    if alert_show is None or alert_show.empty:
        save_csv(pd.DataFrame(columns=RANK_ALERT_COLUMNS), alerts_path)
    else:
        save_csv(alert_show, alerts_path)
    if rollup is None or rollup.empty:
        save_csv(pd.DataFrame(), rollup_path)
    else:
        save_csv(rollup, rollup_path)

    print(f"\nRaw rank    ->  {raw_path}  ({n_raw} rows)")
    print(
        f"Alerts      ->  {alerts_path}  "
        f"({0 if alerts is None or alerts.empty else len(alerts)} rows)"
    )
    print(f"Daily rollup ->  {rollup_path}")
    print(
        "Reproduce: scan_hk_option_flow.py --backtest --source rank "
        f"--preset {args.preset or 'default'} "
        f"--from {valid_days[0].isoformat()} --to {valid_days[-1].isoformat()}"
    )
    return 0


def run_live(quote_ctx, args, since, until):
    print(
        f"Scanning HK listed equity options | source=event market=HK_SECURITY "
        f"preset={args.preset or '(none)'} "
        f"side={args.side} action={args.action} "
        f"min_turnover={args.min_turnover:,.0f} max_dte={args.max_dte} "
        f"cluster_only={bool(args.cluster_only)} "
        f"min_buy_sell_ratio={args.min_buy_sell_ratio if args.min_buy_sell_ratio is not None else '(off)'} "
        f"owners={args.owners or '(all)'} count={args.count} pages={args.pages} "
        f"max_day_num={args.max_day_num if args.max_day_num is not None else '(default)'}"
    )
    print(
        "NOTE: get_option_event only keeps unusual prints for still-listed "
        "contracts; expired near-expiry history is purged. Prefer --source rank "
        "for backtests."
    )

    ret, msg, frames, all_count = fetch_events(quote_ctx, args, with_ticker_type=True)
    server_side_action = args.action != "ALL"
    if ret != RET_OK and args.action != "ALL":
        print(
            f"WARNING: TICKER_TYPE filter rejected by OpenD ({msg}); "
            "retrying and filtering locally.",
            file=sys.stderr,
        )
        ret, msg, frames, all_count = fetch_events(
            quote_ctx, args, with_ticker_type=False
        )
        server_side_action = False

    if ret != RET_OK:
        print(f"ERROR: get_option_event failed: {msg}", file=sys.stderr)
        return 1

    raw = concat_frames(frames)
    enriched = enrich(raw)
    filtered = apply_client_filters(enriched, args, since=since, until=until)
    if not server_side_action and args.action != "ALL":
        print(f"NOTE: {args.action} side was filtered client-side.")

    if args.min_buy_sell_ratio is not None:
        filtered, kept_days, dropped_days = apply_imbalance_gate(
            quote_ctx, args, filtered, since, until
        )
        print(
            f"near-expiry buy/sell imbalance gate (buy >= "
            f"{args.min_buy_sell_ratio:g} x sell): kept {kept_days} owner-day(s), "
            f"dropped {dropped_days}."
        )

    if args.cluster_only:
        clusters, membership = find_clusters(
            filtered,
            window_min=args.cluster_window_min,
            min_prints=args.cluster_min_prints,
            min_turnover=args.cluster_min_turnover,
            same_strike=bool(args.same_strike_cluster),
        )
        alerts = attach_alerts(filtered, membership, min_turnover=float("inf"))
        if (
            alerts is not None
            and not alerts.empty
            and (
                args.min_underlying_chg is not None
                or args.require_stock_not_up is not None
            )
        ):
            owners = sorted(alerts["owner_code"].dropna().unique().tolist())
            prior = fetch_prior_closes(
                quote_ctx, owners,
                alerts["fill_date"].min(), alerts["fill_date"].max(),
            )
            alerts, chg_skipped = apply_underlying_chg(
                alerts, prior, args.min_underlying_chg, args.require_stock_not_up
            )
            print(
                f"underlying chg gate: kept {len(alerts)}, "
                f"skipped {0 if chg_skipped is None else len(chg_skipped)} "
                f"(min={args.min_underlying_chg}, max={args.require_stock_not_up})."
            )
        show = display_frame(alerts)
        if not show.empty:
            show["alert_reason"] = alerts["alert_reason"].values
            show["cluster_id"] = alerts["cluster_id"].values
            n_qual = 0 if clusters.empty else int(clusters["qualifies"].sum())
            print(
                f"cluster-only live: {n_qual} qualifying cluster(s), "
                f"{len(show)} alert print(s) of {len(filtered)} near-expiry."
            )
        filtered = alerts
    else:
        show = display_frame(filtered)
    if not show.empty:
        show = show.sort_values("turnover", ascending=False, ignore_index=True)
    n_raw = 0 if raw is None or raw.empty else len(raw)
    empty_msg = "No matching prints found in the fetched window."
    if show.empty and n_raw and args.max_dte is not None and not enriched.empty:
        d = pd.to_numeric(enriched.get("dte_at_fill"), errors="coerce").dropna()
        if not d.empty:
            empty_msg = (
                f"No matching prints in the fetched window ({n_raw} fetched, "
                f"dte_at_fill {int(d.min())}..{int(d.max())}, none in "
                f"[0, {args.max_dte}]). Widen --count/--pages/--max-day-num "
                f"or raise --max-dte. OpenD `dte` is from today and is ignored."
            )
    print_table(show, empty_msg=empty_msg)

    out_dir = Path(args.out_dir) if args.out_dir else SCRIPT_DIR
    csv_path = args.csv or str(out_dir / LIVE_CSV_NAME)
    save_csv(show, csv_path)
    print(
        f"\nFetched {n_raw} raw event(s)"
        + (f" (all_count={all_count})" if all_count is not None else "")
        + f"; {len(show)} after client filters  ->  saved to {csv_path}"
    )
    return 0


def run_backtest(quote_ctx, args, since, until):
    print(
        f"BACKTEST HK option flow | source=event market=HK_SECURITY "
        f"preset={args.preset or '(none)'} "
        f"side={args.side} action={args.action} "
        f"min_turnover={args.min_turnover:,.0f} max_dte={args.max_dte} "
        f"cluster_only={bool(args.cluster_only)} "
        f"same_strike={bool(args.same_strike_cluster)} "
        f"min_buy_sell_ratio={args.min_buy_sell_ratio if args.min_buy_sell_ratio is not None else '(off)'} "
        f"cluster_window={args.cluster_window_min:g}min "
        f"cluster_min_prints={args.cluster_min_prints} "
        f"cluster_min_turnover={args.cluster_min_turnover:,.0f} "
        f"owners={args.owners or '(all)'} count={args.count} pages={args.pages} "
        f"max_day_num={args.max_day_num}"
    )
    print(
        "WARNING: get_option_event purges expired near-expiry contracts. "
        "Year-long event backtests look empty. Use --source rank for 日频/回测."
    )
    if args.cluster_only:
        print(
            "Alert rule: matching side/action, computed DTE in [0, max_dte], "
            "and member of a qualifying cluster (size-only prints ignored)."
        )
    else:
        print(
            "Alert rule: matching side/action, computed DTE in [0, max_dte], and "
            "(turnover >= min_turnover OR member of a qualifying cluster)."
        )
    print(
        "Cluster: same owner + same expiry ISO week + same calendar day, "
        "span <= cluster_window_min, and "
        "(prints >= cluster_min_prints OR sum(turnover) >= cluster_min_turnover)."
    )
    print("DTE is computed from strike_time/option_code vs fill_time; OpenD dte is ignored.")

    ret, msg, frames, all_count = fetch_events(quote_ctx, args, with_ticker_type=True)
    if ret != RET_OK and args.action != "ALL":
        print(
            f"WARNING: TICKER_TYPE filter rejected by OpenD ({msg}); "
            "retrying and filtering locally.",
            file=sys.stderr,
        )
        ret, msg, frames, all_count = fetch_events(
            quote_ctx, args, with_ticker_type=False
        )
    if ret != RET_OK:
        print(f"ERROR: get_option_event failed: {msg}", file=sys.stderr)
        return 1

    raw = concat_frames(frames)
    enriched = enrich(raw)
    near = apply_client_filters(enriched, args, since=since, until=until)

    if args.min_buy_sell_ratio is not None:
        near, kept_days, dropped_days = apply_imbalance_gate(
            quote_ctx, args, near, since, until
        )
        print(
            f"imbalance gate (buy >= {args.min_buy_sell_ratio:g} x sell): "
            f"kept {kept_days} owner-day(s), dropped {dropped_days}."
        )

    clusters, membership = find_clusters(
        near,
        window_min=args.cluster_window_min,
        min_prints=args.cluster_min_prints,
        min_turnover=args.cluster_min_turnover,
        same_strike=bool(args.same_strike_cluster),
    )
    size_floor = (
        float("inf") if args.cluster_only else args.min_turnover
    )
    alerts = attach_alerts(near, membership, min_turnover=size_floor)

    skipped = pd.DataFrame()
    if (
        args.min_underlying_chg is not None
        or args.require_stock_not_up is not None
    ) and not alerts.empty:
        owners = sorted(alerts["owner_code"].dropna().unique().tolist())
        d0 = alerts["fill_date"].min()
        d1 = alerts["fill_date"].max()
        prior = fetch_prior_closes(quote_ctx, owners, d0, d1)
        alerts, skipped = apply_underlying_chg(
            alerts, prior, args.min_underlying_chg, args.require_stock_not_up
        )
        print(
            f"underlying chg gate (min={args.min_underlying_chg}, "
            f"max={args.require_stock_not_up}): "
            f"kept {len(alerts)}, skipped {len(skipped)}"
        )

    n_raw = 0 if raw is None or raw.empty else len(raw)
    tmin = enriched["fill_dt"].min() if not enriched.empty else None
    tmax = enriched["fill_dt"].max() if not enriched.empty else None
    print(
        f"\nFetched {n_raw} raw event(s)"
        + (f" (all_count={all_count})" if all_count is not None else "")
        + f"  fill_time {fmt_ts(tmin)} -> {fmt_ts(tmax)}"
    )
    print(f"Near-expiry matching prints: {len(near)}")
    n_qual = 0 if clusters.empty else int(clusters["qualifies"].sum())
    print(f"Qualifying clusters: {n_qual}")
    print(f"Alert prints: {0 if alerts is None or alerts.empty else len(alerts)}")

    print("\n--- Qualifying clusters ---")
    print_cluster_table(clusters)

    print("\n--- Alert prints ---")
    alert_show = pd.DataFrame()
    if alerts is not None and not alerts.empty:
        alert_show = display_frame(alerts)
        alert_show["alert_reason"] = alerts["alert_reason"].values
        alert_show["cluster_id"] = alerts["cluster_id"].values
        alert_show["cluster_prints"] = alerts["cluster_prints"].values
        alert_show["cluster_turnover"] = alerts["cluster_turnover"].values
        alert_show = alert_show.sort_values("time", kind="mergesort").reset_index(drop=True)
    print_table(alert_show, empty_msg="No alerts under the current rules.")

    rollup = daily_rollup(near)
    print("\n--- Daily rollup (near-expiry matching side, top owners by turnover) ---")
    print_daily_rollup(rollup)

    print_minimax_check(enriched, near, alerts, clusters)

    others = other_alert_owners(alerts)
    print("\n--- Owners that would have alerted in the focus week ---")
    if others.empty:
        print("None.")
    else:
        show = others.rename(
            columns={"owner_code": "owner", "n_alerts": "alerts"}
        )
        print_table(show)

    kline, kerr = fetch_focus_kline(quote_ctx)
    if kerr:
        print(f"\nNOTE: {FOCUS_OWNER} kline fetch failed: {kerr}")
    else:
        print_focus_kline(kline)

    out_dir = Path(args.out_dir) if args.out_dir else SCRIPT_DIR
    raw_path = out_dir / RAW_CSV_NAME
    alerts_path = out_dir / ALERTS_CSV_NAME
    rollup_path = out_dir / ROLLUP_CSV_NAME

    raw_export = enriched.copy()
    if not raw_export.empty:
        # Drop helper objects that don't CSV well; keep computed DTE.
        drop_cols = [c for c in ("row_indices",) if c in raw_export.columns]
        raw_export = raw_export.drop(columns=drop_cols)
    save_csv(raw_export, raw_path)

    if alert_show.empty:
        save_csv(pd.DataFrame(columns=ALERT_COLUMNS), alerts_path)
    else:
        save_csv(alert_show, alerts_path)

    if rollup is None or rollup.empty:
        save_csv(pd.DataFrame(), rollup_path)
    else:
        export_r = rollup.copy()
        export_r["fill_date"] = export_r["fill_date"].astype(str)
        save_csv(export_r, rollup_path)

    print(f"\nRaw events  ->  {raw_path}  ({n_raw} rows)")
    print(
        f"Alerts      ->  {alerts_path}  "
        f"({0 if alerts is None or alerts.empty else len(alerts)} rows)"
    )
    print(f"Daily rollup ->  {rollup_path}")
    return 0


def main(argv=None):
    args = apply_mode_defaults(apply_preset_and_defaults(parse_args(argv)))

    count_max = 200 if args.source == "rank" else 300
    if not (1 <= args.count <= count_max):
        print(
            f"ERROR: --count must be in [1, {count_max}] for OpenD {args.source}.",
            file=sys.stderr,
        )
        return 2
    if args.pages < 1:
        print("ERROR: --pages must be >= 1.", file=sys.stderr)
        return 2
    if args.max_day_num is not None and args.max_day_num < 0:
        print("ERROR: --max-day-num must be >= 0.", file=sys.stderr)
        return 2
    if args.sleep < 0:
        print("ERROR: --sleep must be >= 0.", file=sys.stderr)
        return 2

    since = parse_ymd(args.since, "--since")
    until = parse_ymd(args.until, "--until")
    start = parse_ymd(args.from_date, "--from") or since
    end = parse_ymd(args.to_date, "--to") or until

    quote_ctx = None
    try:
        quote_ctx = OpenQuoteContext(host=args.host, port=args.port)
        if args.source == "rank":
            if args.backtest:
                return run_backtest_rank(quote_ctx, args, start, end)
            return run_live_rank(quote_ctx, args, start, end)
        if args.backtest:
            return run_backtest(quote_ctx, args, since, until)
        return run_live(quote_ctx, args, since, until)
    except Exception as exc:  # noqa: BLE001 - surface any runtime failure
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if quote_ctx is not None:
            quote_ctx.close()
            print("Quote context closed. (No trades were placed.)")


if __name__ == "__main__":
    sys.exit(main())
