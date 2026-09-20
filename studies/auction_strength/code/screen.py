"""Screen / rank a list of DayBars for auction+trend quality."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

try:
    from .metrics import DayBar, BarScore, score_bar
except ImportError:
    from metrics import DayBar, BarScore, score_bar


@dataclass(frozen=True)
class ScreenResult:
    scores: List[BarScore]
    selected: List[BarScore]
    trade_date: str
    min_auction_pct: float
    min_day_pct: float
    min_combined: float

    def to_rows(self) -> List[dict]:
        rows = []
        selected_syms = {s.symbol for s in self.selected}
        for s in self.scores:
            d = asdict(s)
            d["selected"] = s.symbol in selected_syms
            rows.append(d)
        return rows


def screen_bars(
    bars: Sequence[DayBar],
    *,
    min_auction_pct: float = 0.015,
    min_day_pct: float = 0.025,
    min_combined: float = 55.0,
    require_nonneg_follow: bool = True,
) -> ScreenResult:
    scores = [score_bar(b) for b in bars]
    scores.sort(key=lambda s: s.combined_score, reverse=True)
    selected: List[BarScore] = []
    for s in scores:
        if s.auction_pct < min_auction_pct:
            continue
        if s.day_pct < min_day_pct:
            continue
        if require_nonneg_follow and s.follow_through_pct < 0:
            continue
        if s.combined_score < min_combined:
            continue
        selected.append(s)
    dates = {b.trade_date for b in bars}
    trade_date = next(iter(dates)) if len(dates) == 1 else "mixed"
    return ScreenResult(
        scores=scores,
        selected=selected,
        trade_date=trade_date,
        min_auction_pct=min_auction_pct,
        min_day_pct=min_day_pct,
        min_combined=min_combined,
    )


def load_fixture(path: Path) -> List[DayBar]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [DayBar.from_mapping(row) for row in data["bars"]]


def format_table(result: ScreenResult) -> str:
    lines = [
        f"trade_date={result.trade_date}  "
        f"thresholds auction>={result.min_auction_pct:.2%} "
        f"day>={result.min_day_pct:.2%} combined>={result.min_combined:.1f}",
        "",
        f"{'sel':>3} {'symbol':<10} {'name':<8} {'auc%':>7} {'day%':>7} "
        f"{'ft%':>7} {'cloc':>5} {'comb':>6} label",
        "-" * 78,
    ]
    selected = {s.symbol for s in result.selected}
    for s in result.scores:
        mark = "Y" if s.symbol in selected else "."
        lines.append(
            f"{mark:>3} {s.symbol:<10} {s.name:<8} {s.auction_pct*100:6.2f}% "
            f"{s.day_pct*100:6.2f}% {s.follow_through_pct*100:6.2f}% "
            f"{s.close_location:5.2f} {s.combined_score:6.1f} {s.label}"
        )
    lines.append("")
    lines.append(f"selected {len(result.selected)}/{len(result.scores)}")
    return "\n".join(lines)
