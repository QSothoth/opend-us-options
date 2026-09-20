"""Read-only OpenD capture during A/HK opening auction (T-1 minute window).

Does not place orders. Requires a running OpenD (default 127.0.0.1:11111)
with quote rights for the requested market.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class AuctionTick:
    ts: str
    code: str
    last_price: Optional[float] = None
    open_price: Optional[float] = None
    prev_close: Optional[float] = None
    volume: Optional[float] = None
    turnover: Optional[float] = None
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None
    bid_vol: Optional[float] = None
    ask_vol: Optional[float] = None
    bid_ask_ratio: Optional[float] = None
    volume_ratio: Optional[float] = None
    outstanding_shares: Optional[float] = None
    circular_market_val: Optional[float] = None
    market_state: Optional[str] = None
    # order book aggregates (optional)
    book_bid_vol: Optional[float] = None
    book_ask_vol: Optional[float] = None
    book_imbalance: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)


def _f(row: Any, key: str) -> Optional[float]:
    try:
        v = row[key]
        if v is None or (isinstance(v, float) and v != v):
            return None
        return float(v)
    except Exception:
        return None


def _connect(host: str, port: int):
    try:
        import futu as ft
    except ImportError as e:
        raise RuntimeError(
            "futu-api not installed. pip install futu-api on the machine running OpenD."
        ) from e
    ctx = ft.OpenQuoteContext(host=host, port=port)
    return ft, ctx


def fetch_snapshot_ticks(
    codes: Sequence[str],
    *,
    host: str = "127.0.0.1",
    port: int = 11111,
    with_order_book: bool = True,
) -> List[AuctionTick]:
    """One-shot richest snapshot (+ L1/L2 book if subscribed) for codes."""
    ft, ctx = _connect(host, port)
    ticks: List[AuctionTick] = []
    try:
        # market state
        state_map: Dict[str, str] = {}
        ret_s, st = ctx.get_market_state(list(codes))
        if ret_s == ft.RET_OK and st is not None:
            for _, r in st.iterrows():
                state_map[str(r["code"])] = str(r.get("market_state", ""))

        if with_order_book:
            # best-effort subscribe; ignore failures (BMP limits etc.)
            try:
                ctx.subscribe(list(codes), [ft.SubType.ORDER_BOOK, ft.SubType.QUOTE])
                time.sleep(0.3)
            except Exception:
                pass

        ret, data = ctx.get_market_snapshot(list(codes))
        if ret != ft.RET_OK:
            raise RuntimeError(f"get_market_snapshot failed: {data}")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for _, row in data.iterrows():
            code = str(row["code"])
            tick = AuctionTick(
                ts=now,
                code=code,
                last_price=_f(row, "last_price"),
                open_price=_f(row, "open_price"),
                prev_close=_f(row, "prev_close_price"),
                volume=_f(row, "volume"),
                turnover=_f(row, "turnover"),
                bid_price=_f(row, "bid_price"),
                ask_price=_f(row, "ask_price"),
                bid_vol=_f(row, "bid_vol"),
                ask_vol=_f(row, "ask_vol"),
                bid_ask_ratio=_f(row, "bid_ask_ratio"),
                volume_ratio=_f(row, "volume_ratio"),
                outstanding_shares=_f(row, "outstanding_shares"),
                circular_market_val=_f(row, "circular_market_val"),
                market_state=state_map.get(code),
            )
            if with_order_book:
                try:
                    ret_b, book = ctx.get_order_book(code, num=10)
                    if ret_b == ft.RET_OK and book:
                        bid_side = book.get("Bid") or book.get("bid") or []
                        ask_side = book.get("Ask") or book.get("ask") or []
                        bv = sum(float(x[1]) for x in bid_side if len(x) > 1)
                        av = sum(float(x[1]) for x in ask_side if len(x) > 1)
                        tick.book_bid_vol = bv
                        tick.book_ask_vol = av
                        denom = bv + av
                        tick.book_imbalance = ((bv - av) / denom) if denom > 0 else None
                except Exception:
                    pass
            # keep a slim raw for audit
            tick.raw = {
                k: (None if (isinstance(v, float) and v != v) else v)
                for k, v in row.to_dict().items()
                if k
                in (
                    "name",
                    "update_time",
                    "turnover_rate",
                    "amplitude",
                    "sec_status",
                )
            }
            ticks.append(tick)
    finally:
        ctx.close()
    return ticks


def sample_window(
    codes: Sequence[str],
    *,
    duration_sec: float = 20.0,
    interval_sec: float = 2.0,
    host: str = "127.0.0.1",
    port: int = 11111,
) -> Dict[str, List[dict]]:
    """Burst-sample during the last minute of auction; returns per-code tick lists."""
    end = time.time() + duration_sec
    series: Dict[str, List[dict]] = {c: [] for c in codes}
    while time.time() < end:
        ticks = fetch_snapshot_ticks(codes, host=host, port=port, with_order_book=True)
        for t in ticks:
            series.setdefault(t.code, []).append(asdict(t))
        time.sleep(interval_sec)
    return series


def save_capture(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
