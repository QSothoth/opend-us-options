import numpy as np, pickle
from balstat import bal, diff, side_mean
R = pickle.load(open('marks_w8.pkl', 'rb'))
TAGS = ('T1', 'T2', 'T3')
def rep(rows, tag, label=''):
    yes = [r for r in rows if r[tag]]; no = [r for r in rows if not r[tag]]
    by, bn = bal(yes, 'fclose'), bal(no, 'fclose'); d, td = diff(yes, no, 'fclose'); d30, t30 = diff(yes, no, 'f30')
    sides = [(side_mean(yes, s, 'fclose')[0], side_mean(no, s, 'fclose')[0]) for s in (1, -1)]
    print('  %s%-3s good %4.1f%% | close good %+6.2f (t%+4.1f) bad %+6.2f (t%+4.1f) diff %+6.2f t%+4.1f | f30 diff %+5.2f t%+4.1f | BUY %+6.2f/%+6.2f SELL %+6.2f/%+6.2f'
          % (label, tag, 100 * len(yes) / max(len(rows), 1), by[0], by[1], bn[0], bn[1], d, td, d30, t30, sides[0][0], sides[0][1], sides[1][0], sides[1][1]))
    return dict(td=td, d30=d30, sides=sides)
G = lambda seg, gran: [r for r in R if r['seg'] == seg and (r['tpb'] >= 2) == gran]
print('== SELECTION granular'); tr = {k: rep(G('train', True), k) for k in TAGS}
print('== selection coarse ~ (report)'); [rep(G('train', False), k) for k in TAGS]
go = [k for k in TAGS if tr[k]['td'] >= 2]; print('to validation:', go)
print('== VALIDATION granular'); va = {k: rep(G('valid', True), k) for k in go}
print('== validation coarse ~ (report)'); [rep(G('valid', False), k) for k in go]
for k in TAGS:
    if k not in go: print(k, 'FAIL at selection'); continue
    v = va[k]; g = [v['td'] >= 2, v['d30'] > 0, all(a > b for a, b in v['sides'])]
    print(k, g, 'PASS' if all(g) else 'FAIL', '(Bonferroni t>=2.4: %s)' % (v['td'] >= 2.4))
print('== within D+/D- (granular, report)')
for seg in ('train', 'valid'):
    for dv in (True, False):
        rows = [r for r in G(seg, True) if r['daily'] == dv]
        for k in go: rep(rows, k, '%s D%s ' % (seg, '+' if dv else '-'))
