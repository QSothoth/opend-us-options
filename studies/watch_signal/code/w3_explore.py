import numpy as np, pickle, lab, sys
R = pickle.load(open('marks_granular.pkl', 'rb'))
seg = sys.argv[1] if len(sys.argv) > 1 else 'train'
T = [r for r in R if r['seg'] == seg]
def rep(name, rows, m='f30'):
    s = lab.summarize([r[m] for r in rows], [r['day'] for r in rows])
    if s['n'] < 30: return
    print('  %-26s n=%6d mean=%+6.2fbp hit=%5.1f%% po=%.2f t=%+5.1f' % (name, s['n'], s['mean_bp'], 100 * s['hit'], s['payoff'], s['t_day']))
print('ALL'); rep('all', T); rep('all trade', T, 'trade'); rep('all fclose', T, 'fclose'); rep('all f60', T, 'f60'); rep('all f15', T, 'f15')
print('TRIGGER')
for nm, fn in (('bos only', lambda r: r['bos'] and not r['fvg'] and not r['sweep']), ('fvg only', lambda r: r['fvg'] and not r['bos']),
               ('bos+fvg', lambda r: r['bos'] and r['fvg']), ('sweep', lambda r: r['sweep'])):
    rep(nm, [r for r in T if fn(r)])
print('TIER'); [rep(t, [r for r in T if r['tier'] == t]) for t in ('plain', 'vol')]
def q(key, k=5):
    v = np.array([r[key] for r in T], float); ok = ~np.isnan(v)
    qs = np.quantile(v[ok], np.linspace(0, 1, k + 1)); print(key.upper())
    for j in range(k):
        rep('%s q%d [%.3g,%.3g)' % (key, j + 1, qs[j], qs[j + 1]), [r for r, x in zip(T, v) if qs[j] <= x < qs[j + 1] or (j == k - 1 and x == qs[-1])])
for key in ('tpb', 'rsi', 'vr', 'pos', 'dv', 'ema', 'r15', 'rday', 'mday', 'm15', 'atrbp', 'stopdist', 't'):
    q(key)
