"""Score one symbol-day: auction proxy + same-day path quality."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class DayBar:
    symbol: str
    name: str
    market: str  # HK | A
    trade_date: str
    prev_close: float
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    turnover: Optional[float] = None
    # Optional true auction fields when OpenD/IEP available
    auction_price: Optional[float] = None
    auction_volume: Optional[float] = None
    notes: str = ""

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "DayBar":
        return cls(
            symbol=str(m["symbol"]),
            name=str(m.get("name", "")),
            market=str(m["market"]),
            trade_date=str(m["trade_date"]),
            prev_close=float(m["prev_close"]),
            open=float(m["open"]),
            high=float(m["high"]),
            low=float(m["low"]),
            close=float(m["close"]),
            volume=_opt_float(m.get("volume")),
            turnover=_opt_float(m.get("turnover")),
            auction_price=_opt_float(m.get("auction_price")),
            auction_volume=_opt_float(m.get("auction_volume")),
            notes=str(m.get("notes", "")),
        )


def _opt_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    return float(v)


@dataclass(frozen=True)
class BarScore:
    symbol: str
    name: str
    market: str
    trade_date: str
    auction_pct: float  # vs prev close (IEP if present else open)
    day_pct: float
    follow_through_pct: float  # (close - open) / prev_close
    close_location: float  # 0=low .. 1=high; 0.5 if flat range
    gap_and_go: bool
    gap_and_hold: bool
    auction_rank_score: float
    day_trend_score: float
    combined_score: float
    label: str
    auction_field: str  # "auction_price" | "open_proxy"
    notes: str


def score_bar(bar: DayBar) -> BarScore:
    if bar.prev_close <= 0:
        raise ValueError(f"{bar.symbol}: prev_close must be > 0")
    if bar.high < bar.low:
        raise ValueError(f"{bar.symbol}: high < low")

    if bar.auction_price is not None:
        px = bar.auction_price
        auction_field = "auction_price"
    else:
        px = bar.open
        auction_field = "open_proxy"

    auction_pct = (px - bar.prev_close) / bar.prev_close
    day_pct = (bar.close - bar.prev_close) / bar.prev_close
    follow = (bar.close - bar.open) / bar.prev_close
    span = bar.high - bar.low
    close_loc = 0.5 if span <= 0 else (bar.close - bar.low) / span

    # Path character (coarse, intentional)
    gap_and_go = auction_pct >= 0.02 and follow >= 0.03 and close_loc >= 0.85
    gap_and_hold = (
        auction_pct >= 0.015
        and day_pct >= auction_pct * 0.8
        and bar.low >= bar.prev_close * 0.99
        and close_loc >= 0.55
    )

    # Rank components in ~0..100 style (not calibrated probabilities)
    auction_rank = _clip(50 + auction_pct * 800, 0, 100)  # +5% ~ 90
    day_trend = _clip(
        0.45 * _clip(50 + day_pct * 500, 0, 100)
        + 0.35 * _clip(50 + follow * 600, 0, 100)
        + 0.20 * (close_loc * 100),
        0,
        100,
    )
    combined = 0.45 * auction_rank + 0.55 * day_trend

    if gap_and_go:
        label = "gap_and_go"
    elif gap_and_hold:
        label = "gap_and_hold"
    elif auction_pct >= 0.01 and day_pct >= 0.02 and follow >= 0:
        label = "auction_strong_day_ok"
    elif auction_pct >= 0.01 and day_pct < auction_pct * 0.5:
        label = "auction_fade"
    else:
        label = "other"

    return BarScore(
        symbol=bar.symbol,
        name=bar.name,
        market=bar.market,
        trade_date=bar.trade_date,
        auction_pct=auction_pct,
        day_pct=day_pct,
        follow_through_pct=follow,
        close_location=close_loc,
        gap_and_go=gap_and_go,
        gap_and_hold=gap_and_hold,
        auction_rank_score=auction_rank,
        day_trend_score=day_trend,
        combined_score=combined,
        label=label,
        auction_field=auction_field,
        notes=bar.notes,
    )


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
