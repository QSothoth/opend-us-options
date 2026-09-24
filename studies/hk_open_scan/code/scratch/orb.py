#!/usr/bin/env python3
"""R3: the full ORB structure (Zarattini/Barbon/Aziz), HK Connect, long only.

Rules are frozen in notes/R3_PREREG.md and were written before this ran.

  opening range = 09:31-09:35   (09:30 is the auction print, not a traded minute)
  direction     = that 5min closes up
  entry         = first 1m close above the range high, at that close
  stop          = entry - 0.5 * ATR14   (ATR from the 14 PRIOR sessions)
  exit          = stop, else the 15:59 close
  cost          = 2 ticks round trip, from the measured price_spread
  R             = 0.5 * ATR14, so every trade is reported in risk units
"""
import csv, gzip, json, statistics as st
from collections import defaultdict

SP = '/tmp/claude-0/-opt-opend-us-options/7723de8d-a5a3-4cd6-aced-8a5248bfed6c/scratchpad/'
OR_START, OR_END, EXIT = '09:31', '09:35', '15:59'
ATR_N, RVOL_N, STOP_MULT = 14, 14, 0.5
MAX_TICK_BP, TICKS = 15.0, 2
GATES = [('E0_no_filter', 0.0), ('E1_rvol>=1', 1.0), ('E2_rvol>=2', 2.0),
         ('E3_rvol>=5', 5.0), ('E4_rvol>=10', 10.0), ('E5_rvol>=30', 30.0)]

ticks = {r['code']: float(r['price_spread']) for r in csv.DictReader(open(SP + 'ticks.csv'))}
hkc = set(json.load(open(SP + 'hkconnect.json')))

days = defaultdict(dict)
with gzip.open(SP + 'hk1m_long.csv.gz', 'rt') as fh:
    for r in csv.DictReader(fh):
        if r['code'] in hkc:
            days[(r['code'], r['time_key'][:10])][r['time_key'][11:16]] = (
                float(r['open']), float(r['high']), float(r['low']),
                float(r['close']), float(r['volume']), float(r['last_close']))

per_code = defaultdict(list)
for (code, date), bars in days.items():
    if ticks.get(code) and EXIT in bars and len(bars) >= 60:
        per_code[code].append((date, bars))

# market benchmark: equal-weight return from each minute to the close, per date
minutes = sorted({t for bars in days.values() for t in bars})
idx = {t: i for i, t in enumerate(minutes)}
mkt = defaultdict(lambda: defaultdict(list))
for code, sessions in per_code.items():
    for date, bars in sessions:
        last = bars[EXIT][3]
        for t, b in bars.items():
            if b[3] > 0:
                mkt[date][t].append(last / b[3] - 1.0)
market = {d: {t: sum(v) / len(v) for t, v in m.items() if len(v) >= 20} for d, m in mkt.items()}

trades = []
for code, sessions in per_code.items():
    sessions.sort()
    tick = ticks[code]
    tr_hist, rv_hist = [], []
    for date, bars in sessions:
        ts = sorted(bars)
        hi = max(b[1] for b in bars.values())
        lo = min(b[2] for b in bars.values())
        prev_close = bars[ts[0]][5]
        tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
        orb = [bars[t] for t in ts if OR_START <= t <= OR_END]
        or_vol = sum(b[4] for b in orb) if orb else 0.0

        atr = sum(tr_hist[-ATR_N:]) / ATR_N if len(tr_hist) >= ATR_N else None
        base = sum(rv_hist[-RVOL_N:]) / RVOL_N if len(rv_hist) >= RVOL_N else None
        tr_hist.append(tr)
        rv_hist.append(or_vol)
        if atr is None or not base or atr <= 0 or len(orb) < 3:
            continue
        # long only: the opening range must close up
        if orb[-1][3] <= orb[0][0]:
            continue
        or_high = max(b[1] for b in orb)
        after = [(t, bars[t]) for t in ts if t > OR_END and t <= EXIT]
        entry = entry_t = None
        for t, b in after:
            if b[3] > or_high:
                entry, entry_t = b[3], t
                break
        if entry is None or entry <= 0 or tick / entry * 1e4 > MAX_TICK_BP:
            continue
        stop = entry - STOP_MULT * atr
        risk = STOP_MULT * atr
        if risk <= 0:
            continue
        exit_px = bars[EXIT][3]
        for t, b in after:
            if t > entry_t and b[2] <= stop:
                exit_px = stop
                break
        cost = TICKS * tick
        net = (exit_px - entry - cost) / entry
        m = market.get(date, {}).get(entry_t)
        trades.append({
            'date': date, 'rvol': or_vol / base,
            'net_R': (exit_px - entry - cost) / risk,
            'net_bp': net * 1e4,
            'excess_bp': (net - m) * 1e4 if m is not None else None,
            'cost_over_R': cost / risk,
            'risk_bp': risk / entry * 1e4,
        })

print('breakout long setups found: %d  (over %d stock-days)\n' % (len(trades), len(days)))
hdr = '%-14s %7s %9s %8s %9s %9s %8s %8s  %s'
print(hdr % ('candidate', 'n', 'net_R', 't(day)', 'net_bp', 'excess_bp', 'win%', 'cost/R', 'verdict'))
for name, g in GATES:
    sel = [t for t in trades if t['rvol'] >= g]
    if len(sel) < 2:
        print('%-14s %7d   (too few)' % (name, len(sel)))
        continue
    by_day = defaultdict(list)
    for t in sel:
        by_day[t['date']].append(t['net_R'])
    daily = [sum(v) / len(v) for v in by_day.values()]
    mR = sum(t['net_R'] for t in sel) / len(sel)
    tt = 0.0
    if len(daily) > 1 and st.stdev(daily) > 0:
        tt = (sum(daily) / len(daily)) / (st.stdev(daily) / len(daily) ** 0.5)
    ex = [t['excess_bp'] for t in sel if t['excess_bp'] is not None]
    ok = 'PASS' if (mR > 0 and tt > 2 and len(sel) >= 100) else ''
    print(hdr % (name, len(sel), '%.4f' % mR, '%.2f' % tt,
                 '%.1f' % (sum(t['net_bp'] for t in sel) / len(sel)),
                 '%.1f' % (sum(ex) / len(ex)) if ex else '-',
                 '%.1f%%' % (sum(1 for t in sel if t['net_R'] > 0) / len(sel) * 100),
                 '%.2f' % (sum(t['cost_over_R'] for t in sel) / len(sel)), ok))

if trades:
    rs = sorted(t['net_R'] for t in trades)
    print('\nnet_R distribution (all breakouts): p10 %.2f  p50 %.2f  p90 %.2f  max %.2f' % (
        rs[len(rs) // 10], rs[len(rs) // 2], rs[9 * len(rs) // 10], rs[-1]))
    print('median stop distance: %.0f bp of price; median cost/R: %.2f' % (
        st.median(t['risk_bp'] for t in trades), st.median(t['cost_over_R'] for t in trades)))

# --- robustness diagnostics on the one gate that turned positive (E3) ---
print('\n--- E3 (rvol>=5) robustness ---')
e3 = sorted([t for t in trades if t['rvol'] >= 5.0], key=lambda t: t['net_R'])
if e3:
    n = len(e3)
    print('n=%d over %d distinct days' % (n, len({t['date'] for t in e3})))
    print('mean net_R                 %+.4f' % (sum(t['net_R'] for t in e3) / n))
    print('drop the single best trade %+.4f' % (sum(t['net_R'] for t in e3[:-1]) / (n - 1)))
    print('drop the best 3 trades     %+.4f' % (sum(t['net_R'] for t in e3[:-3]) / (n - 3)))
    print('median net_R               %+.4f' % st.median(t['net_R'] for t in e3))
    best = e3[-1]
    print('best trade: %+.2fR on %s (rvol %.1f)' % (best['net_R'], best['date'], best['rvol']))
    bd = defaultdict(list)
    for t in e3:
        bd[t['date']].append(t['net_R'])
    dm = sorted((sum(v) / len(v), d) for d, v in bd.items())
    print('day means: worst %+.2f (%s), best %+.2f (%s), days>0: %d/%d' % (
        dm[0][0], dm[0][1], dm[-1][0], dm[-1][1],
        sum(1 for m, _ in dm if m > 0), len(dm)))
