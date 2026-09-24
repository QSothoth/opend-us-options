#!/usr/bin/env python3
"""Does a HIGHER volume gate make the intraday signal clear the cost?

Pre-registered before looking at any output:
  - rvol = bar volume / mean of the PRIOR 20 bars (denominator excludes the bar
    itself -- WATCH_REVIEW section 2 showed the self-inclusion squashes a real
    10x down to 6.9x, which biases exactly the high gates we are testing).
  - entry: close of the trigger bar.  exit: +15m / +30m / session close.
  - direction: follow the bar (close>open -> long).  The inverse is also
    reported, since watch/README measured t=-5.16 on the original direction.
  - cost: 2 ticks round trip, same as R1.  Liquidity filter: tick <= 15bp.
  - verdict: net mean > 0 AND t > 2.
"""
import csv, gzip, statistics as st
from collections import defaultdict

SP = '/tmp/claude-0/-opt-opend-us-options/7723de8d-a5a3-4cd6-aced-8a5248bfed6c/scratchpad/'
LOOKBACK, GATES = 20, [1.5, 3, 5, 10, 20, 50]
MAX_TICK_BP, TICKS = 15.0, 2

ticks = {r['code']: float(r['price_spread']) for r in csv.DictReader(open(SP + 'ticks.csv'))}

days = defaultdict(list)
with gzip.open(SP + 'hk1m_long.csv.gz', 'rt') as fh:
    for r in csv.DictReader(fh):
        days[(r['code'], r['time_key'][:10])].append(
            (r['time_key'][11:16], float(r['open']), float(r['close']), float(r['volume'])))

# gate -> horizon -> list of net returns (follow direction)
res = {g: defaultdict(list) for g in GATES}
for (code, date), bars in days.items():
    tick = ticks.get(code)
    if not tick:
        continue
    bars.sort()
    bars = [b for b in bars if b[0] >= '09:31']          # 09:30 is the auction print
    if len(bars) < 60:
        continue
    closes = [b[2] for b in bars]
    vols = [b[3] for b in bars]
    for i in range(LOOKBACK, len(bars) - 15):
        t, o, c, v = bars[i]
        if c <= 0 or o <= 0 or c == o:
            continue
        if tick / c * 1e4 > MAX_TICK_BP:
            continue
        prior = vols[i - LOOKBACK:i]                      # excludes bar i
        avg = sum(prior) / LOOKBACK
        if avg <= 0:
            continue
        rvol = v / avg
        sign = 1.0 if c > o else -1.0
        cost = TICKS * tick / c
        for g in GATES:
            if rvol < g:
                continue
            for name, j in (('+15m', i + 15), ('+30m', min(i + 30, len(bars) - 1)),
                            ('close', len(bars) - 1)):
                res[g][name].append((sign * (closes[j] / c - 1.0), cost))

def stat(pairs):
    if len(pairs) < 2:
        return None
    gross = [g for g, _ in pairs]
    cost = sum(c for _, c in pairs) / len(pairs)
    m = sum(gross) / len(gross)
    sd = st.stdev(gross)
    t = m / (sd / len(gross) ** 0.5) if sd > 0 else 0.0
    return m * 1e4, t, cost * 1e4, sum(1 for g in gross if g > 0) / len(gross), len(gross)

print('%-6s %-6s %9s %9s %7s %8s %8s %8s' % (
    'gate', 'exit', 'n', 'GROSS_bp', 't', 'cost_bp', 'net_bp', 'hit'))
for g in GATES:
    for name in ('+15m', '+30m', 'close'):
        s = stat(res[g][name])
        if not s:
            continue
        m, t, cost, hit, n = s
        print('%-6s %-6s %9d %9.2f %7.2f %8.1f %8.1f %7.1f%%' % (
            '>=%g' % g, name, n, m, t, cost, m - cost, hit * 100))
    print()
