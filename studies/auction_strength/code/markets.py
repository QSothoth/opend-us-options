"""Market calendars for opening auction (times in Asia/Shanghai = HKT)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuctionWindow:
    market: str  # "HK" | "A"
    cancel_ok: str
    no_cancel: str
    match: str
    continuous_start: str
    notes: str


HK_POS = AuctionWindow(
    market="HK",
    cancel_ok="09:00–09:15",
    no_cancel="09:15–09:20",
    match="09:20–≤09:22 random IEP",
    continuous_start="09:30",
    notes="IEP/IEV via POS; open often set ~5–10m before A-share 09:25.",
)

A_SHARE = AuctionWindow(
    market="A",
    cancel_ok="09:15–09:20",
    no_cancel="09:20–09:25",
    match="09:25 fixed",
    continuous_start="09:30",
    notes="主板涨跌停框架；开盘价 09:25 定点。",
)


def normalize_symbol(raw: str) -> tuple[str, str]:
    """Return (market, futu-style code) e.g. ('HK', 'HK.00100')."""
    s = raw.strip().upper().replace(" ", "")
    if s.endswith(".HK"):
        num = s[:-3].zfill(5)
        return "HK", f"HK.{num}"
    if s.startswith("HK."):
        return "HK", f"HK.{s[3:].zfill(5)}"
    if s.startswith("SH.") or s.startswith("SZ."):
        return "A", s
    if len(s) == 6 and s.isdigit():
        # crude A-share board guess
        if s.startswith(("6", "9")):
            return "A", f"SH.{s}"
        return "A", f"SZ.{s}"
    if len(s) in (4, 5) and s.isdigit():
        return "HK", f"HK.{s.zfill(5)}"
    raise ValueError(f"unrecognized symbol: {raw!r}")
