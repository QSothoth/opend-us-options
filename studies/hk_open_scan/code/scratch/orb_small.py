#!/usr/bin/env python3
"""R4: the R3 ORB rules, unchanged, on the 522 mid/small-cap HK Connect names.

Only the universe and the data source changed (see notes/R4_PREREG.md).
Frozen before the run; the +-0.08R no-conclusion band is part of the contract.
"""
import csv, gzip, json, statistics as st
from collections import defaultdict

SP = '/tmp/claude-0/-opt-opend-us-options/d8c681a4-9a79-4bc6-b926-a7c7b297957c/scratchpad/'
ATR_N, RVOL_N, STOP_MULT = 14, 14, 0.5
MAX_TICK_BP, TICKS = 15.0, 2
MIN_TURNOVER, TURN_N = 5e7, 20
NOISE_R = 0.08                     # Yahoo-vs-OpenD intraday noise, in risk units
GATES = [('F0_no_filter', 0.0), ('F1_rvol>=1', 1.0), ('F2_rvol>=2', 2.0),
         ('F3_rvol>=5', 5.0), ('F4_rvol>=10', 10.0), ('F5_rvol>=30', 30.0)]


def hk_tick(p):
    """HKEX standard spread table. Measured OpenD ticks run ~half this, so the
    cost modelled here is deliberately the conservative side."""
    for hi, t in ((0.25, 0.001), (0.50, 0.005), (10, 0.01), (20, 0.02), (100, 0.05),
                  (200, 0.1), (500, 0.2), (1000, 0.5), (2000, 1.0), (5000, 2.0)):
        if p < hi:
            return t
    return 5.0


days = defaultdict(dict)
with gzip.open(SP + 'hk5m_small.csv.gz', 'rt') as fh:
    for r in csv.DictReader(fh):
        days[(r['code'], r['time_key'][:10])][r['time_key'][11:16]] = (
            float(r['open']), float(r['high']), float(r['low']),
            float(r['close']), float(r['volume']))

per_code = defaultdict(list)
for (code, date), bars in days.items():
    if len(bars) >= 30:
        per_code[code].append((date, bars))

mkt = defaultdict(lambda: defaultdict(list))
for code, sessions in per_code.items():
    for date, bars in sessions:
        last = bars[max(bars)][3]
        for t, b in bars.items():
            if b[3] > 0:
                mkt[date][t].append(last / b[3] - 1.0)
market = {d: {t: sum(v) / len(v) for t, v in m.items() if len(v) >= 20}
          for d, m in mkt.items()}

trades = []
for code, sessions in per_code.items():
    sessions.sort()
    tr_hist, rv_hist, turn_hist = [], [], []
    for date, bars in sessions:
        ts = sorted(bars)
        hi = max(b[1] for b in bars.values())
        lo = min(b[2] for b in bars.values())
        prev_close = tr_hist and None
        pc = sessions[max(0, [d for d, _ in sessions].index(date) - 1)][1]
        prev_c = pc[max(pc)][3] if pc is not bars else bars[ts[0]][0]
        tr = max(hi - lo, abs(hi - prev_c), abs(lo - prev_c))
        orb = bars[ts[0]]
        turn = sum(b[3] * b[4] for b in bars.values())

        atr = sum(tr_hist[-ATR_N:]) / ATR_N if len(tr_hist) >= ATR_N else None
        base = sum(rv_hist[-RVOL_N:]) / RVOL_N if len(rv_hist) >= RVOL_N else None
        bturn = sum(turn_hist[-TURN_N:]) / min(len(turn_hist), TURN_N) if turn_hist else None
        tr_hist.append(tr); rv_hist.append(orb[4]); turn_hist.append(turn)
        if atr is None or not base or atr <= 0 or bturn is None or bturn < MIN_TURNOVER:
            continue
        if orb[3] <= orb[0]:                       # long only: opening bar must close up
            continue
        or_high = orb[1]
        entry = entry_t = None
        for t in ts[1:]:
            if bars[t][3] > or_high:
                entry, entry_t = bars[t][3], t
                break
        if entry is None or entry <= 0:
            continue
        tick = hk_tick(entry)
        if tick / entry * 1e4 > MAX_TICK_BP:
            continue
        risk = STOP_MULT * atr
        if risk <= 0:
            continue
        stop = entry - risk
        exit_px = bars[ts[-1]][3]
        for t in ts:
            if t > entry_t and bars[t][2] <= stop:
                exit_px = stop
                break
        cost = TICKS * tick
        net = (exit_px - entry - cost) / entry
        m = market.get(date, {}).get(entry_t)
        trades.append({'date': date, 'code': code, 'rvol': orb[4] / base,
                       'net_R': (exit_px - entry - cost) / risk, 'net_bp': net * 1e4,
                       'excess_bp': (net - m) * 1e4 if m is not None else None,
                       'cost_over_R': cost / risk, 'risk_bp': risk / entry * 1e4})

man = json.load(open(SP + 'hk5m_small_manifest.json'))
print('universe fetched: %d ok / %d empty / %d failed' % (
    len(man['ok']), len(man['empty']), len(man['failed'])))
print('codes with usable sessions: %d   stock-days: %d   breakout longs: %d\n' % (
    len(per_code), len(days), len(trades)))

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
    if abs(mR) <= NOISE_R:
        v = 'no-conclusion (within +-%.2fR noise)' % NOISE_R
    elif mR > 0 and tt > 2 and len(sel) >= 100:
        v = 'PASS'
    else:
        v = ''
    print(hdr % (name, len(sel), '%.4f' % mR, '%.2f' % tt,
                 '%.1f' % (sum(t['net_bp'] for t in sel) / len(sel)),
                 '%.1f' % (sum(ex) / len(ex)) if ex else '-',
                 '%.1f%%' % (sum(1 for t in sel if t['net_R'] > 0) / len(sel) * 100),
                 '%.2f' % (sum(t['cost_over_R'] for t in sel) / len(sel)), v))

if trades:
    rs = sorted(t['net_R'] for t in trades)
    print('\nnet_R: p10 %.2f  p50 %.2f  p90 %.2f  max %.2f' % (
        rs[len(rs) // 10], rs[len(rs) // 2], rs[9 * len(rs) // 10], rs[-1]))
    print('median stop %.0f bp of price; median cost/R %.2f' % (
        st.median(t['risk_bp'] for t in trades), st.median(t['cost_over_R'] for t in trades)))
    rv = sorted(t['rvol'] for t in trades)
    print('rvol distribution: p50 %.1f  p90 %.1f  p99 %.1f  max %.1f' % (
        rv[len(rv) // 2], rv[9 * len(rv) // 10], rv[99 * len(rv) // 100], rv[-1]))

print('\n--- F3 (rvol>=5) robustness, the same check that killed R3 E3 ---')
f3 = sorted([t for t in trades if t['rvol'] >= 5.0], key=lambda t: t['net_R'])
if f3:
    n = len(f3)
    print('n=%d  distinct codes=%d  distinct days=%d' % (
        n, len({t['code'] for t in f3}), len({t['date'] for t in f3})))
    for k in (0, 1, 3, 5):
        sub = f3[:n - k] if k else f3
        print('  drop best %d: net_R %+.4f' % (k, sum(t['net_R'] for t in sub) / len(sub)))
    print('  median net_R %+.4f   win%% %.1f' % (
        st.median(t['net_R'] for t in f3),
        sum(1 for t in f3 if t['net_R'] > 0) / n * 100))
    print('  top 5 trades:')
    for t in f3[-5:][::-1]:
        print('    %+7.2fR  %s %s  rvol %.1f' % (t['net_R'], t['code'], t['date'], t['rvol']))
    share = sum(t['net_R'] for t in f3[-3:]) / sum(t['net_R'] for t in f3)
    print('  top 3 trades contribute %.0f%% of total net_R' % (share * 100))
    bd = defaultdict(list)
    for t in f3:
        bd[t['date']].append(t['net_R'])
    dm = [sum(v) / len(v) for v in bd.values()]
    print('  day means >0: %d/%d' % (sum(1 for m in dm if m > 0), len(dm)))
    bc = defaultdict(list)
    for t in f3:
        bc[t['code']].append(t['net_R'])
    top = sorted(bc.items(), key=lambda kv: -sum(kv[1]))[:3]
    print('  most profitable codes: %s' % ', '.join(
        '%s(%d trades, %+.1fR)' % (c, len(v), sum(v)) for c, v in top))
