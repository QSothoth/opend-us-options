#!/usr/bin/env python3
"""The same long-only test, with the two things that would fake a PASS removed.

1. BETA.  Holding anything to the close earns the day's drift.  Every trade is
   therefore measured against the equal-weight return of the SAME universe over
   the SAME minutes -- excess, not raw.
2. OVERLAP.  Many bars on one stock-day share one closing price, so a per-trade
   t is inflated.  Trades are averaged per DAY first; the t is across the 69
   days, which is the unit that is actually independent.
"""
import csv, gzip, json, statistics as st
from collections import defaultdict

SP = '/tmp/claude-0/-opt-opend-us-options/7723de8d-a5a3-4cd6-aced-8a5248bfed6c/scratchpad/'
LOOKBACK, GATES = 20, [1.5, 3, 5, 10, 20]
MAX_TICK_BP, TICKS = 15.0, 2

ticks = {r['code']: float(r['price_spread']) for r in csv.DictReader(open(SP + 'ticks.csv'))}
hkc = set(json.load(open(SP + 'hkconnect.json')))

days = defaultdict(list)
with gzip.open(SP + 'hk1m_long.csv.gz', 'rt') as fh:
    for r in csv.DictReader(fh):
        if r['code'] in hkc:
            days[(r['code'], r['time_key'][:10])].append(
                (r['time_key'][11:16], float(r['open']), float(r['close']), float(r['volume'])))

# per date: minute -> [returns of every eligible name from that minute to close]
by_date = defaultdict(dict)
for (code, date), bars in days.items():
    if not ticks.get(code):
        continue
    bars.sort()
    bars = [b for b in bars if b[0] >= '09:31']
    if len(bars) < 60:
        continue
    by_date[date][code] = bars

market = {}          # (date, minute_index) -> equal-weight return to the close
for date, book in by_date.items():
    acc = defaultdict(list)
    for code, bars in book.items():
        closes = [b[2] for b in bars]
        last = closes[-1]
        for i, c in enumerate(closes):
            if c > 0:
                acc[i].append(last / c - 1.0)
    market[date] = {i: sum(v) / len(v) for i, v in acc.items() if len(v) >= 20}

daily = {(g, d): defaultdict(list) for g in GATES for d in ('UP', 'DOWN')}
for date, book in by_date.items():
    mkt = market[date]
    for code, bars in book.items():
        tick = ticks[code]
        closes = [b[2] for b in bars]
        vols = [b[3] for b in bars]
        last = closes[-1]
        for i in range(LOOKBACK, len(bars) - 15):
            t, o, c, v = bars[i]
            if c <= 0 or o <= 0 or c == o or tick / c * 1e4 > MAX_TICK_BP or i not in mkt:
                continue
            avg = sum(vols[i - LOOKBACK:i]) / LOOKBACK
            if avg <= 0:
                continue
            rvol = v / avg
            d = 'UP' if c > o else 'DOWN'
            excess = (last / c - 1.0) - mkt[i] - TICKS * tick / c
            for g in GATES:
                if rvol >= g:
                    daily[(g, d)][date].append(excess)

print('%-6s %-5s %6s %9s %10s %7s %8s  %s' % (
    'gate', 'dir', 'days', 'trades', 'EXCESS_bp', 't', 'day_hit', 'verdict'))
for g in GATES:
    for d in ('UP', 'DOWN'):
        per_day = [sum(v) / len(v) for v in daily[(g, d)].values() if v]
        n_tr = sum(len(v) for v in daily[(g, d)].values())
        if len(per_day) < 10:
            continue
        m = sum(per_day) / len(per_day)
        sd = st.stdev(per_day)
        t = m / (sd / len(per_day) ** 0.5) if sd > 0 else 0.0
        hit = sum(1 for x in per_day if x > 0) / len(per_day)
        ok = 'PASS' if (m > 0 and t > 2) else ''
        print('%-6s %-5s %6d %9d %10.2f %7.2f %7.1f%%  %s' % (
            '>=%g' % g, d, len(per_day), n_tr, m * 1e4, t, hit * 100, ok))
    print()
