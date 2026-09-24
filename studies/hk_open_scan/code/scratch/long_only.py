#!/usr/bin/env python3
"""LONG ONLY, HK Connect names only.  Split by the trigger bar's own direction.

The earlier +-1 test folded "chase the up bar" and "buy the down bar" into one
symmetric number.  Long-only breaks that symmetry, so they are reported apart:

  UP   = volume spike on an up bar, buy it      (chasing)
  DOWN = volume spike on a down bar, buy it     (fading / dip)

Same gates, same cost model (2 ticks round trip), same liquidity filter.
Verdict, fixed before looking: net mean > 0 AND t > 2.
"""
import csv, gzip, statistics as st
from collections import defaultdict

SP = '/tmp/claude-0/-opt-opend-us-options/7723de8d-a5a3-4cd6-aced-8a5248bfed6c/scratchpad/'
LOOKBACK, GATES = 20, [1.5, 3, 5, 10, 20, 50]
MAX_TICK_BP, TICKS = 15.0, 2

ticks = {r['code']: float(r['price_spread']) for r in csv.DictReader(open(SP + 'ticks.csv'))}
hkc = set(__import__('json').load(open(SP + 'hkconnect.json')))
print('HK Connect universe: %d names; with tick data: %d' % (len(hkc), len(hkc & set(ticks))))

days = defaultdict(list)
with gzip.open(SP + 'hk1m_long.csv.gz', 'rt') as fh:
    for r in csv.DictReader(fh):
        if r['code'] in hkc:
            days[(r['code'], r['time_key'][:10])].append(
                (r['time_key'][11:16], float(r['open']), float(r['close']), float(r['volume'])))

res = {(g, d): defaultdict(list) for g in GATES for d in ('UP', 'DOWN')}
codes = set()
for (code, date), bars in days.items():
    tick = ticks.get(code)
    if not tick:
        continue
    bars.sort()
    bars = [b for b in bars if b[0] >= '09:31']
    if len(bars) < 60:
        continue
    codes.add(code)
    closes = [b[2] for b in bars]
    vols = [b[3] for b in bars]
    for i in range(LOOKBACK, len(bars) - 15):
        t, o, c, v = bars[i]
        if c <= 0 or o <= 0 or c == o or tick / c * 1e4 > MAX_TICK_BP:
            continue
        avg = sum(vols[i - LOOKBACK:i]) / LOOKBACK
        if avg <= 0:
            continue
        rvol = v / avg
        d = 'UP' if c > o else 'DOWN'
        cost = TICKS * tick / c
        for g in GATES:
            if rvol >= g:
                for name, j in (('+15m', i + 15), ('+30m', min(i + 30, len(bars) - 1)),
                                ('close', len(bars) - 1)):
                    res[(g, d)][name].append((closes[j] / c - 1.0, cost))   # LONG only

print('codes used: %d, stock-days: %d\n' % (len(codes), len(days)))
print('%-6s %-5s %-6s %9s %9s %7s %8s %8s %8s  %s' % (
    'gate', 'dir', 'exit', 'n', 'gross_bp', 't', 'cost_bp', 'NET_bp', 'hit', 'verdict'))
for g in GATES:
    for d in ('UP', 'DOWN'):
        for name in ('+15m', '+30m', 'close'):
            pairs = res[(g, d)][name]
            if len(pairs) < 30:
                continue
            gross = [x for x, _ in pairs]
            cost = sum(c for _, c in pairs) / len(pairs)
            net = [x - c for x, c in pairs]
            m = sum(net) / len(net)
            sd = st.stdev(net)
            t = m / (sd / len(net) ** 0.5) if sd > 0 else 0.0
            ok = 'PASS' if (m > 0 and t > 2) else ''
            print('%-6s %-5s %-6s %9d %9.2f %7.2f %8.1f %8.1f %7.1f%%  %s' % (
                '>=%g' % g, d, name, len(net), sum(gross) / len(gross) * 1e4, t,
                cost * 1e4, m * 1e4, sum(1 for x in net if x > 0) / len(net) * 100, ok))
    print()
