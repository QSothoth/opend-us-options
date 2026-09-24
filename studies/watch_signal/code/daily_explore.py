import numpy as np, pickle, sys
from collections import defaultdict
R = pickle.load(open('marks_daily.pkl', 'rb'))
seg = sys.argv[1] if len(sys.argv) > 1 else 'train'
T = [r for r in R if r['seg'] == seg]
def bal(rows, m):
    """Side-balanced mean 0.5*(mean_BUY + mean_SELL) in bp, with a day-clustered t:
    the mean is a sum over days of c_d = 0.5*(S_buy,d/N_buy + S_sell,d/N_sell)."""
    per = defaultdict(lambda: {1: [0.0, 0], -1: [0.0, 0]})
    for r in rows:
        x = r[m]
        if x == x:
            per[r['day']][r['side']][0] += x; per[r['day']][r['side']][1] += 1
    N = {s: sum(d[s][1] for d in per.values()) for s in (1, -1)}
    if min(N.values()) == 0: return np.nan, np.nan, sum(N.values())
    c = np.array([0.5 * (d[1][0] / N[1] + d[-1][0] / N[-1]) for d in per.values()])
    b = c.sum()
    t = c.mean() / c.std(ddof=1) * np.sqrt(len(c)) if len(c) > 2 and c.std() > 0 else np.nan
    return b * 1e4, t, sum(N.values())
def rep(name, rows):
    a = bal(rows, 'f30'); c = bal(rows, 'fclose')
    print('   %-30s n=%6d | f30 bal %+6.2f t%+5.1f | close bal %+6.2f t%+5.1f' % (name, a[2], a[0], a[1], c[0], c[1]))
for gname, gf in (('GRANULAR tpb>=2', lambda r: r['tpb'] >= 2), ('COARSE tpb<2', lambda r: r['tpb'] < 2)):
    G = [r for r in T if gf(r)]
    print('==', gname); rep('all', G)
    for v in (1, 0, -1): rep('align(daily stage x side)=%d' % v, [r for r in G if r['align'] == v])
    rep('pdlevel broken', [r for r in G if r['pdlevel']]); rep('pdlevel not', [r for r in G if not r['pdlevel']])
    for key in ('rvol_d1', 'contract5', 'udvol10', 'ret5d', 'ret20d', 'ema20slope', 'gap', 'dist20', 'dayret'):
        v = np.array([r[key] for r in G], float); ok = ~np.isnan(v); qs = np.quantile(v[ok], [0, .2, .4, .6, .8, 1])
        print('  ', key)
        for j in range(5):
            rep('q%d [%.3g,%.3g)' % (j + 1, qs[j], qs[j + 1]), [r for r, x in zip(G, v) if qs[j] <= x < qs[j + 1] or (j == 4 and x == qs[5])])
