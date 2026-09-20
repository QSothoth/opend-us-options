"""Multi-factor auction strength from OpenD capture series (not return-only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

ROW_FIELDS = ("rank", "code", "name", "market", "score", "label", "data_grade")
FACTOR_FIELDS = (
    "premium_pct",
    "path_late_spike",
    "path_stability",
    "volume",
    "turnover",
    "volume_ratio",
    "book_imbalance",
    "bid_ask_ratio",
    "float_turnover",
)


@dataclass
class LiveAuctionScore:
    code: str
    name: str
    market: str
    data_grade: str  # full | partial | thin
    premium_pct: Optional[float]
    path_late_spike: Optional[float]  # last 20% ΔP / full ΔP; high = late chase
    path_stability: Optional[float]  # 1 - normalized std of prices
    turnover: Optional[float]
    volume: Optional[float]
    volume_ratio: Optional[float]
    book_imbalance: Optional[float]
    bid_ask_ratio: Optional[float]
    float_turnover: Optional[float]  # turnover / circular_market_val
    score: float
    label: str  # strong | medium | weak | unknown
    notes: str
    rank: int = 0

    def factors(self) -> Dict[str, Optional[float]]:
        return {name: getattr(self, name) for name in FACTOR_FIELDS}

    def to_row(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "code": self.code,
            "name": self.name,
            "market": self.market,
            "score": self.score,
            "label": self.label,
            "data_grade": self.data_grade,
            "factors": self.factors(),
            "notes": self.notes,
        }


def _last(series: Sequence[dict], key: str) -> Optional[float]:
    for row in reversed(series):
        v = row.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _prices(series: Sequence[dict]) -> List[float]:
    out = []
    for row in series:
        v = row.get("last_price")
        if v is not None:
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                pass
    return out


def score_series(
    code: str,
    series: Sequence[dict],
    *,
    market: str,
    name: str = "",
) -> LiveAuctionScore:
    if not series:
        return LiveAuctionScore(
            code=code,
            name=name,
            market=market,
            data_grade="thin",
            premium_pct=None,
            path_late_spike=None,
            path_stability=None,
            turnover=None,
            volume=None,
            volume_ratio=None,
            book_imbalance=None,
            bid_ask_ratio=None,
            float_turnover=None,
            score=0.0,
            label="unknown",
            notes="no ticks",
        )

    last = series[-1]
    name = name or str((last.get("raw") or {}).get("name") or "")
    prev = _last(series, "prev_close")
    px = _last(series, "last_price")
    premium = ((px - prev) / prev) if (px is not None and prev and prev > 0) else None

    prices = _prices(series)
    late_spike = None
    stability = None
    if len(prices) >= 4 and prices[0] != 0:
        full = prices[-1] - prices[0]
        cut = max(2, len(prices) // 5)
        late = prices[-1] - prices[-cut]
        if abs(full) > 1e-12:
            late_spike = late / full
        else:
            late_spike = 0.0
        mean = sum(prices) / len(prices)
        var = sum((p - mean) ** 2 for p in prices) / len(prices)
        std = var ** 0.5
        # normalize by |mean| roughly
        stability = max(0.0, 1.0 - (std / abs(mean) * 50 if mean else 1.0))

    turnover = _last(series, "turnover")
    volume = _last(series, "volume")
    volume_ratio = _last(series, "volume_ratio")
    book_imbalance = _last(series, "book_imbalance")
    if book_imbalance is None:
        bv, av = _last(series, "bid_vol"), _last(series, "ask_vol")
        if bv is not None and av is not None and (bv + av) > 0:
            book_imbalance = (bv - av) / (bv + av)
    bid_ask_ratio = _last(series, "bid_ask_ratio")
    cmv = _last(series, "circular_market_val")
    float_turnover = (turnover / cmv) if (turnover and cmv and cmv > 0) else None

    # data grade
    has_book = book_imbalance is not None
    has_vol = volume is not None and volume > 0
    has_path = len(prices) >= 4
    if has_path and (has_vol or has_book) and premium is not None:
        grade = "full"
    elif premium is not None and (has_vol or has_book or has_path):
        grade = "partial"
    else:
        grade = "thin"

    # multi-factor score 0..100 (honest: missing factors don't invent strength)
    parts = []
    weights = []

    def add(w: float, x: Optional[float], lo: float, hi: float, invert: bool = False):
        if x is None:
            return
        # map x in [lo,hi] -> 0..100
        t = (x - lo) / (hi - lo) if hi != lo else 0.5
        t = max(0.0, min(1.0, t))
        if invert:
            t = 1.0 - t
        parts.append(t * 100)
        weights.append(w)

    # premium: -2%..+6% maps to score
    add(0.25, premium, -0.02, 0.06)
    # volume_ratio if present (0.5..3)
    add(0.20, volume_ratio, 0.5, 3.0)
    # float turnover (0..1% of circ mkt in auction — rough)
    add(0.15, float_turnover, 0.0, 0.01)
    # book imbalance -0.5..0.5
    add(0.20, book_imbalance, -0.5, 0.5)
    # path: prefer NOT late spike (invert), and stability
    add(0.10, late_spike, 0.0, 1.5, invert=True)
    add(0.10, stability, 0.0, 1.0)

    if not weights:
        score = 0.0
    else:
        score = sum(p * w for p, w in zip(parts, weights)) / sum(weights)

    # thin data: cap score so we never overclaim
    if grade == "thin":
        score = min(score, 40.0)
        notes = "data_grade=thin: mostly price-only; treat as weak evidence"
    elif grade == "partial":
        score = min(score, 75.0)
        notes = "data_grade=partial: missing some volume/book/path"
    else:
        notes = "data_grade=full: premium+path+(volume|book)"

    if score >= 70:
        label = "strong"
    elif score >= 50:
        label = "medium"
    elif score > 0:
        label = "weak"
    else:
        label = "unknown"

    return LiveAuctionScore(
        code=code,
        name=name,
        market=market,
        data_grade=grade,
        premium_pct=premium,
        path_late_spike=late_spike,
        path_stability=stability,
        turnover=turnover,
        volume=volume,
        volume_ratio=volume_ratio,
        book_imbalance=book_imbalance,
        bid_ask_ratio=bid_ask_ratio,
        float_turnover=float_turnover,
        score=round(score, 1),
        label=label,
        notes=notes,
    )


def rank_watchlist(
    capture: Dict[str, List[dict]],
    *,
    market: str,
    names: Optional[Dict[str, str]] = None,
) -> List[LiveAuctionScore]:
    names = names or {}
    scores = [
        score_series(code, series, market=market, name=names.get(code, ""))
        for code, series in capture.items()
    ]
    scores.sort(key=lambda s: s.score, reverse=True)
    for i, s in enumerate(scores, 1):
        s.rank = i
    return scores


def format_live_table(scores: Sequence[LiveAuctionScore]) -> str:
    lines = [
        f"{'rank':>4} {'code':<12} {'name':<8} {'score':>5} {'label':<7} "
        f"{'grade':<7} {'prem%':>7} {'imb':>6} {'vr':>5} {'late':>5}",
        "-" * 80,
    ]
    for s in scores:
        rank = s.rank if s.rank else 0
        prem = f"{s.premium_pct * 100:.2f}" if s.premium_pct is not None else "n/a"
        imb = f"{s.book_imbalance:.2f}" if s.book_imbalance is not None else "n/a"
        vr = f"{s.volume_ratio:.2f}" if s.volume_ratio is not None else "n/a"
        late = f"{s.path_late_spike:.2f}" if s.path_late_spike is not None else "n/a"
        lines.append(
            f"{rank:>4} {s.code:<12} {s.name:<8} {s.score:5.1f} {s.label:<7} "
            f"{s.data_grade:<7} {prem:>7} {imb:>6} {vr:>5} {late:>5}"
        )
    return "\n".join(lines)
