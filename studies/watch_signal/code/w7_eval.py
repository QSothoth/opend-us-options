"""W7: level-reaction marks (PDH/PDL and 15-min opening range). Streams per stock-day."""
import numpy as np, pickle, lab, feat, daily
from gran import ticks_per_bar
from balstat import bal, side_mean
DF = daily.features(daily.load_daily())
CANDS = ('R1', 'R2', 'R3', 'R4', 'R5')
def events(f, g):
    h, l, c = f['h'], f['l'], f['c']; n = len(c)
    S = {k: np.zeros(n, bool) for k in CANDS}; B = {k: np.zeros(n, bool) for k in CANDS}
    pdh, pdl = g['pdh'], g['pdl']
    orh, orl = h[1:16].max(), l[1:16].min()
    for i in range(2, n):
        S['R1'][i] = h[i] >= pdh > c[i] and c[i - 1] < pdh
        B['R1'][i] = l[i] <= pdl < c[i] and c[i - 1] > pdl
        S['R2'][i] = c[i - 1] > pdh >= c[i]
        B['R2'][i] = c[i - 1] < pdl <= c[i]
        if i >= 16:
            S['R3'][i] = h[i] >= orh > c[i] and c[i - 1] < orh
            B['R3'][i] = l[i] <= orl < c[i] and c[i - 1] > orl
            S['R4'][i] = c[i - 1] > orh >= c[i]
            B['R4'][i] = c[i - 1] < orl <= c[i]
    for k in ('R1', 'R2', 'R3', 'R4'):
        S['R5'] |= S[k]; B['R5'] |= B[k]
    out = {}
    for k in CANDS:
        last = {1: -10 ** 9, -1: -10 ** 9}; ev = []
        for i in range(2, n):
            s = 1 if B[k][i] else (-1 if S[k][i] else 0)
            if S[k][i] and B[k][i]:
                s = 0                                  # both at once (tiny range): ambiguous, skip
            if s and i - last[s] >= 15:
                last[s] = i; ev.append((i, s))
        out[k] = ev
    return out
def collect(days, seg, R):
    nsd = {'g': 0, 'c': 0}
    for (code, day), (t, a) in days.items():
        if code.startswith('HK.8'): continue
        g = DF.get((code, day))
        if g is None: continue
        f = feat.build(t, a); c = f['c']; n = len(c); tpb = ticks_per_bar(f)
        gran = tpb[n // 2] >= 2          # stock-day class for density only
        nsd['g' if gran else 'c'] += 1
        for k, ev in events(f, g).items():
            for i, s in ev:
                R.append({'cand': k, 'seg': seg, 'day': day, 'code': code, 'side': s, 'tpb': tpb[i], 't': int(t[i]),
                          'f30': s * (c[i + 31] / c[i + 1] - 1) if i + 31 <= n - 1 else np.nan,
                          'fclose': s * (c[-1] / c[i + 1] - 1) if i + 1 <= n - 1 else np.nan})
    return nsd
if __name__ == '__main__':
    R = []; NSD = {}
    for path, cache, lo, hi, seg in (('old1m.csv.gz', 'days_old.pkl', '2025-12-01', '2026-05-29', 'train'),
                                     ('hk1m_long.csv.gz', 'days.pkl', '2026-06-01', '2026-08-14', 'train'),
                                     ('hk1m_long.csv.gz', 'days.pkl', '2026-08-17', '2026-09-21', 'valid'),
                                     ('new1m.csv.gz', 'days_new.pkl', '2026-09-22', '2026-09-23', 'valid')):
        d = lab.load(path, cache); d = {k: v for k, v in d.items() if lo <= k[1] <= hi}
        x = collect(d, seg, R)
        for k in x: NSD[(seg, k)] = NSD.get((seg, k), 0) + x[k]
        print('loaded', path, lo, len(R), flush=True)
    pickle.dump((R, NSD), open('w7_events.pkl', 'wb'))
    base = pickle.load(open('marks_daily.pkl', 'rb'))
    def report(seg, gran):
        rows = [r for r in R if r['seg'] == seg and (r['tpb'] >= 2) == gran]
        nsd = NSD[(seg, 'g' if gran else 'c')]
        b0 = bal([r for r in base if r['seg'] == seg and (r['tpb'] >= 2) == gran], 'f30')
        print('== %s %s  (existing marks bal f30 %+.2f t%+.1f)' % (seg, 'granular' if gran else 'coarse ~', b0[0], b0[1]))
        res = {}
        for k in CANDS:
            x = [r for r in rows if r['cand'] == k]
            a, c = bal(x, 'f30'), bal(x, 'fclose')
            sb, ss = side_mean(x, 1, 'f30'), side_mean(x, -1, 'f30')
            res[k] = dict(dens=len(x) / nsd, b30=a[0], t30=a[1], bc=c[0], tc=c[1], buy=sb[0], sell=ss[0])
            print('  %s %5.2f/sd n=%6d | f30 bal %+6.2f t%+4.1f | close bal %+6.2f t%+4.1f | BUY %+6.2f SELL %+6.2f'
                  % (k, len(x) / nsd, len(x), a[0], a[1], c[0], c[1], sb[0], ss[0]))
        return res, b0
    tr, _ = report('train', True); report('train', False)
    elig = [k for k in CANDS if tr[k]['dens'] >= 0.1]
    pick = max(elig, key=lambda k: tr[k]['t30']); print('PICK', pick)
    va, b0 = report('valid', True); report('valid', False)
    v = va[pick]
    gates = [v['b30'] > 0 and v['t30'] >= 2, v['bc'] >= 0, v['buy'] > 0 and v['sell'] > 0, v['b30'] > b0[0]]
    print('gates', pick, gates, 'PASS' if all(gates) else 'FAIL')
