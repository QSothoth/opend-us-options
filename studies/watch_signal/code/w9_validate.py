"""W9 validation on 2025-06..2025-11 (first look), plus the W6 K5 replication."""
import pickle, lab, daily
from balstat import bal, diff, side_mean
import w7_eval, extract_marks_daily
DF = w7_eval.DF
d = lab.load('h2025_1m.csv.gz', 'days_h2025.pkl')
d = {k: v for k, v in d.items() if '2025-06-02' <= k[1] <= '2025-11-28'}
print('stock-days', len(d), 'days', len({k[1] for k in d}))
R = []; nsd = w7_eval.collect(d, 'h2025', R)
M = extract_marks_daily.rows_for(d, 'h2025')
pickle.dump((R, nsd, M), open('w9_h2025.pkl', 'wb'))
gm = [m for m in M if m['tpb'] >= 2]
base = bal(gm, 'fclose'); print('existing marks granular close bal %+.2f t%+.1f n=%d' % (base[0], base[1], base[2]))
g = [r for r in R if r['tpb'] >= 2]
V = {'V1': [r for r in g if r['cand'] == 'R5'], 'V2': [r for r in g if r['cand'] == 'R4'],
     'V3': [r for r in g if r['cand'] == 'R5' and DF[(r['code'], r['day'])]['ret5d'] * r['side'] <= 0]}
for k, x in V.items():
    c, a = bal(x, 'fclose'), bal(x, 'f30'); b, s = side_mean(x, 1, 'fclose')[0], side_mean(x, -1, 'fclose')[0]
    print('%s n=%6d dens=%.2f/sd | close bal %+6.2f t%+4.1f | f30 bal %+6.2f t%+4.1f | BUY %+6.2f SELL %+6.2f'
          % (k, len(x), len(x) / max(nsd['g'], 1), c[0], c[1], a[0], a[1], b, s))
    if k == 'V3':
        gates = [c[0] > 0 and c[1] >= 2, b > 0 and s > 0, c[0] > base[0]]
        print('W9 gates V3', gates, 'PASS' if all(gates) else 'FAIL')
cm = [r for r in R if r['tpb'] < 2 and r['cand'] == 'R5' and DF[(r['code'], r['day'])]['ret5d'] * r['side'] <= 0]
c = bal(cm, 'fclose'); print('V3 coarse ~ close bal %+.2f t%+.1f n=%d (report)' % c)
print('== W6 K5 replication on 2025-06..11')
for label, rows in (('granular', gm), ('coarse ~', [m for m in M if m['tpb'] < 2])):
    k5 = lambda m: m['ret5d'] <= 0 and not m['pdlevel']
    yes = [m for m in rows if k5(m)]; no = [m for m in rows if not k5(m)]
    y, n_, df = bal(yes, 'fclose'), bal(no, 'fclose'), diff(yes, no, 'fclose')
    print('  %s D+ %+6.2f t%+4.1f | D- %+6.2f t%+4.1f | diff %+6.2f t%+4.1f | BUY %+6.2f/%+6.2f SELL %+6.2f/%+6.2f | f30 diff %+.2f'
          % (label, y[0], y[1], n_[0], n_[1], df[0], df[1], side_mean(yes, 1, 'fclose')[0], side_mean(no, 1, 'fclose')[0],
             side_mean(yes, -1, 'fclose')[0], side_mean(no, -1, 'fclose')[0], diff(yes, no, 'f30')[0]))
