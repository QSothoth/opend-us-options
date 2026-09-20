"""Read-only OpenD capture during A/HK opening auction (T-1 minute window).

Uses OpenQuoteContext only. Does not place orders, unlock trading, or open a
trade context. Requires a running OpenD (default 127.0.0.1:11111) with quote
rights for the requested market.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11111

Clock = Callable[[], float]
Sleeper = Callable[[float], None]


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


def _reject_trade_ctx(ctx: Any) -> None:
    name = type(ctx).__name__
    if "Trade" in name:
        raise RuntimeError("read-only quote session: trade context rejected")


def _connect(host: str, port: int):
    try:
        import futu as ft
    except ImportError as e:
        raise RuntimeError(
            "futu-api not installed. pip install futu-api on the machine running OpenD."
        ) from e
    ctx = ft.OpenQuoteContext(host=host, port=port)
    _reject_trade_ctx(ctx)
    return ft, ctx


class QuoteSession:
    """One OpenQuoteContext reused for a whole pulse window."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        ctx: Any = None,
        ft: Any = None,
    ):
        self.host = host
        self.port = port
        self._ctx = ctx
        self._ft = ft
        self._owns = ctx is None

    def __enter__(self) -> "QuoteSession":
        if self._ctx is None:
            self._ft, self._ctx = _connect(self.host, self.port)
        elif self._ft is None:
            raise TypeError("ft module is required when ctx is provided")
        _reject_trade_ctx(self._ctx)
        return self

    def __exit__(self, *exc: object) -> bool:
        if self._owns and self._ctx is not None:
            try:
                self._ctx.close()
            finally:
                self._ctx = None
        return False

    @property
    def ctx(self) -> Any:
        return self._ctx

    @property
    def ft(self) -> Any:
        return self._ft


def _try_subscribe(ft: Any, ctx: Any, codes: Sequence[str], sleep: Sleeper) -> None:
    try:
        ctx.subscribe(list(codes), [ft.SubType.ORDER_BOOK, ft.SubType.QUOTE])
        sleep(0.3)
    except Exception:
        pass


def _ticks_from_ctx(
    ft: Any,
    ctx: Any,
    codes: Sequence[str],
    *,
    with_order_book: bool,
) -> List[AuctionTick]:
    state_map: Dict[str, str] = {}
    ret_s, st = ctx.get_market_state(list(codes))
    if ret_s == ft.RET_OK and st is not None:
        for _, r in st.iterrows():
            state_map[str(r["code"])] = str(r.get("market_state", ""))

    ret, data = ctx.get_market_snapshot(list(codes))
    if ret != ft.RET_OK:
        raise RuntimeError(f"get_market_snapshot failed: {data}")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ticks: List[AuctionTick] = []
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
    return ticks


def fetch_snapshot_ticks(
    codes: Sequence[str],
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    with_order_book: bool = True,
    ctx: Any = None,
    ft: Any = None,
    subscribe: bool = True,
    sleep: Sleeper = time.sleep,
) -> List[AuctionTick]:
    """One-shot snapshot (+ order book if available).

    Pass ``ctx``/``ft`` to reuse a connection already opened for this pulse.
    When ``ctx`` is provided this function does **not** close it.
    """
    with QuoteSession(host, port, ctx=ctx, ft=ft) as sess:
        if with_order_book and subscribe:
            _try_subscribe(sess.ft, sess.ctx, codes, sleep=sleep)
        return _ticks_from_ctx(sess.ft, sess.ctx, codes, with_order_book=with_order_book)


def sample_window(
    codes: Sequence[str],
    *,
    duration_sec: float = 20.0,
    interval_sec: float = 2.0,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    ctx: Any = None,
    ft: Any = None,
    clock: Clock = time.time,
    sleep: Sleeper = time.sleep,
) -> Dict[str, List[dict]]:
    """Burst-sample during the last minute of auction; returns per-code tick lists.

    Opens **one** quote connection for the whole window, subscribes once, then
    reuses it for every snapshot. Always takes at least one sample.
    """
    with QuoteSession(host, port, ctx=ctx, ft=ft) as sess:
        _try_subscribe(sess.ft, sess.ctx, codes, sleep=sleep)
        deadline = clock() + duration_sec
        series: Dict[str, List[dict]] = {c: [] for c in codes}
        while True:
            ticks = fetch_snapshot_ticks(
                codes,
                ctx=sess.ctx,
                ft=sess.ft,
                with_order_book=True,
                subscribe=False,
                sleep=sleep,
            )
            for t in ticks:
                series.setdefault(t.code, []).append(asdict(t))
            remaining = deadline - clock()
            if remaining <= 0:
                break
            sleep(min(interval_sec, remaining))
        return series


def save_capture(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_capture(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"capture must be a JSON object: {path}")
    return data
