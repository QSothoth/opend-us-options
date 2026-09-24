"""W5: session-average vs trailing-N ticks-per-bar as the `~` tag. Streams per stock-day."""
import numpy as np, lab, feat, sig2
from collections import defaultdict
KEYS = ('S0', 'L15', 'L30', 'L60')
def dens(f):
    tk = np.array([sig2.hk_tick(p) for p in f['c']]); r = (f['h'] - f['l']) / tk; r[0] = 0
    n = len(r); cs = np.cumsum(r); idx = np.arange(n)
    out = {'S0': cs / np.maximum(idx, 1)}
    for N in (15, 30, 60):
        lo = np.maximum(idx - N, 0)          # bars lo+1..i, never bar 0
        out['L%d' % N] = (cs - cs[lo]) / np.maximum(idx - lo, 1)
    return out
def collect(days):
    R = defaultdict(lambda: ([], []))
    for (code, day), (t, a) in days.items():
        if code.startswith('HK.8'): continue
        f = feat.build(t, a); c = f['c']; n = len(c); D = dens(f)
        d1 = np.full(n, np.nan); d1[:n - 31] = c[31:] / c[1:n - 30] - 1
        for i, s, tier in feat.watcher_marks(f):
            if np.isnan(d1[i]): continue
            for k in KEYS:
                g = 'hi' if D[k][i] >= 2 else 'lo'
                R[(k, g, s)][0].append(s * d1[i]); R[(k, g, s)][1].append(day)
    return R
def bal(R, k, g):
    a, b = lab.summarize(*R[(k, g, 1)]), lab.summarize(*R[(k, g, -1)])
    return (a['mean_bp'] + b['mean_bp']) / 2, a['n'] + b['n']
def report(R, label):
    print('==', label); res = {}
    for k in KEYS:
        (hi, nh), (lo, nl) = bal(R, k, 'hi'), bal(R, k, 'lo')
        res[k] = (hi, lo, hi - lo)
        print('  %-4s  >=2: n=%6d bal=%+6.2fbp | <2: n=%6d bal=%+6.2fbp | diff=%+5.2f' % (k, nh, hi, nl, lo, hi - lo))
    return res
if __name__ == '__main__':
    segs = {'sel': [], 'val': []}
    Rs = {'sel': defaultdict(lambda: ([], [])), 'val': defaultdict(lambda: ([], []))}
    for path, cache, lo, hi, seg in (('old1m.csv.gz', 'days_old.pkl', '2025-12-01', '2026-05-29', 'sel'),
                                     ('hk1m_long.csv.gz', 'days.pkl', '2026-06-01', '2026-08-14', 'sel'),
                                     ('hk1m_long.csv.gz', 'days.pkl', '2026-08-17', '2026-09-21', 'val'),
                                     ('new1m.csv.gz', 'days_new.pkl', '2026-09-22', '2026-09-23', 'val')):
        d = lab.load(path, cache); d = {k: v for k, v in d.items() if lo <= k[1] <= hi}
        for key, v in collect(d).items():
            Rs[seg][key][0].extend(v[0]); Rs[seg][key][1].extend(v[1])
        print('loaded', path, lo, flush=True)
    sel = report(Rs['sel'], 'SELECTION 2025-12-01..2026-08-14')
    pick = max(('L15', 'L30', 'L60'), key=lambda k: sel[k][2]); print('PICK', pick)
    val = report(Rs['val'], 'VALIDATION 2026-08-17..09-23')
    g1 = val[pick][2] > val['S0'][2]; g2 = val[pick][1] < 0; g3 = val[pick][0] >= val['S0'][0]
    print('gates: diff>S0 %s | lo<0 %s | hi>=S0 hi %s -> %s' % (g1, g2, g3, 'PASS' if g1 and g2 and g3 else 'FAIL'))
