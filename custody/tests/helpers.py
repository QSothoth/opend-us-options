"""Test fixtures: hand-built 1m paths for mechanics tests only.

These are *not* market data and must never be used to evaluate a strategy
(AGENTS.md: evaluation uses real frozen Releases only).
"""
import json
from datetime import datetime, time, timedelta
from pathlib import Path

from custody.dataset import write_bars, write_checksums
from custody.marketdata import Bar
from custody.models import ET, Session

DAY = '2026-09-14'


def session(day=DAY, close=time(16)):
    d = datetime.fromisoformat(day)
    return Session(day, datetime.combine(d, time(9, 30), tzinfo=ET), datetime.combine(d, close, tzinfo=ET))


def path_bars(closes, code='US.SPY', day=DAY, wick=0.02, volume=1000.0):
    """One 1m bar per close; open = previous close, small symmetric wicks."""
    s = session(day)
    bars, prev = [], closes[0]
    for i, close in enumerate(closes, start=1):
        o = prev
        bars.append(Bar(code, s.opens + timedelta(minutes=i), o, max(o, close) + wick, min(o, close) - wick,
                        close, volume))
        prev = close
    return bars


def piecewise(points, length=390):
    """Linear path through (minute, price) anchors; minute 1..length."""
    points = sorted(points)
    out = []
    for m in range(1, length + 1):
        for (m0, p0), (m1, p1) in zip(points, points[1:]):
            if m0 <= m <= m1:
                out.append(p0 + (p1 - p0) * (m - m0) / (m1 - m0) if m1 > m0 else p1)
                break
        else:
            out.append(points[-1][1] if m > points[-1][0] else points[0][1])
    return out


def option_bars(underlying_closes, strike, right, code, day=DAY, every=1):
    """A crude option tape for plumbing: intrinsic + decaying time value, traded every N minutes."""
    s = session(day)
    bars = []
    n = len(underlying_closes)
    for i, u in enumerate(underlying_closes, start=1):
        if i % every:
            continue
        intrinsic = max(0.0, (u - strike) if right == 'CALL' else (strike - u))
        price = round(intrinsic + 1.5 * (1 - i / (n + 1)) + 0.05, 4)
        bars.append(Bar(code, s.opens + timedelta(minutes=i), price, price * 1.02, price * 0.98, price, 10.0))
    return bars


def write_dataset(root, cases, name='test-dataset'):
    """cases: list of dicts with symbol, contract, trade_date, underlying (closes), option (bars)."""
    root = Path(root)
    docs = []
    for case in cases:
        write_bars(root / 'underlying' / (case['symbol'] + '.csv'), path_bars(case['underlying'], case['symbol'], case['trade_date']))
        write_bars(root / 'option' / (case['contract'] + '.csv'), case['option'])
        doc = {k: case[k] for k in ('symbol', 'contract', 'trade_date')}
        doc.update({k: case[k] for k in ('prev_close', 'selection', 'direction', 'right') if k in case})
        docs.append(doc)
    (root / 'cases.json').write_text(json.dumps({'cases': docs}, indent=2))
    (root / 'manifest.json').write_text(json.dumps({'dataset': name, 'role': 'train/custody'}))
    write_checksums(root)
    return root
