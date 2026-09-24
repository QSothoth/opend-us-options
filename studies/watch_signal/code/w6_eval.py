import numpy as np, pickle
from collections import defaultdict
R = pickle.load(open('marks_daily.pkl', 'rb'))
CANDS = {'K1': lambda r: r['ret5d'] <= 0, 'K2': lambda r: r['align'] <= 0, 'K3': lambda r: r['udvol10'] <= 0,
         'K4': lambda r: not r['pdlevel'], 'K5': lambda r: r['ret5d'] <= 0 and not r['pdlevel']}
def contrib(rows, m):
    per = defaultdict(lambda: {1: 0.0, -1: 0.0}); N = {1: 0, -1: 0}
    for r in rows:
        x = r[m]
        if x == x: per[r['day']][r['side']] += x; N[r['side']] += 1
    if min(N.values()) == 0: return {}, N
    return {d: 0.5 * (v[1] / N[1] + v[-1] / N[-1]) for d, v in per.items()}, N
def bal(rows, m):
    c, N = contrib(rows, m)
    if not c: return np.nan, np.nan
    a = np.array(list(c.values())); return a.sum() * 1e4, a.mean() / a.std(ddof=1) * np.sqrt(len(a))
def diff(yes, no, m):
    cy, _ = contrib(yes, m); cn, _ = contrib(no, m)
    days = sorted(set(cy) | set(cn)); a = np.array([cy.get(d, 0) - cn.get(d, 0) for d in days])
    return a.sum() * 1e4, a.mean() / a.std(ddof=1) * np.sqrt(len(a))
def side_mean(rows, s, m):
    x = [r[m] for r in rows if r['side'] == s and r[m] == r[m]]; return np.mean(x) * 1e4 if x else np.nan
def evaluate(rows, name):
    out = {}
    print('==', name, 'marks', len(rows))
    for k, fn in CANDS.items():
        yes = [r for r in rows if fn(r)]; no = [r for r in rows if not fn(r)]
        by, ty = bal(yes, 'fclose'); bn, tn = bal(no, 'fclose'); d, td = diff(yes, no, 'fclose')
        y30, _ = bal(yes, 'f30'); n30, _ = bal(no, 'f30')
        sides = [(side_mean(yes, s, 'fclose'), side_mean(no, s, 'fclose')) for s in (1, -1)]
        out[k] = dict(share=len(yes) / len(rows), by=by, bn=bn, d=d, td=td, y30=y30, n30=n30, sides=sides)
        print('  %s share %4.1f%% | close: yes %+6.2f (t%+4.1f) no %+6.2f (t%+4.1f) diff %+6.2f t%+4.1f | f30 yes %+5.2f no %+5.2f | BUY %+6.2f/%+6.2f SELL %+6.2f/%+6.2f'
              % (k, 100 * len(yes) / len(rows), by, ty, bn, tn, d, td, y30, n30, sides[0][0], sides[0][1], sides[1][0], sides[1][1]))
    return out
G = lambda seg, gran: [r for r in R if r['seg'] == seg and ((r['tpb'] >= 2) == gran)]
tr = evaluate(G('train', True), 'SELECTION granular')
elig = [k for k in CANDS if tr[k]['share'] >= 0.3]; pick = max(elig, key=lambda k: tr[k]['td']); print('PICK', pick)
evaluate(G('train', False), 'selection coarse ~ (report only)')
va = evaluate(G('valid', True), 'VALIDATION granular')
evaluate(G('valid', False), 'validation coarse ~ (report only)')
v = va[pick]
g = [v['td'] >= 2, v['by'] > 0, v['y30'] >= v['n30'], all(a > b for a, b in v['sides'])]
print('gates', pick, g, 'PASS' if all(g) else 'FAIL')
