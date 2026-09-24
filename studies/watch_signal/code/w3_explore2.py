import numpy as np, pickle, lab, sys
R = pickle.load(open('marks_granular.pkl', 'rb'))
seg = sys.argv[1] if len(sys.argv) > 1 else 'train'
T = [r for r in R if r['seg'] == seg]
for r in T:
    r['ext'] = r['pos'] if r['side'] > 0 else 1 - r['pos']
    r['rsis'] = (r['rsi'] - 50) * r['side']
def bal(rows, m='f30'):
    """direction-symmetric: mean of BUY-mean and SELL-mean; t from day-clustered per-day balanced sums."""
    out = {}
    for s in (1, -1):
        x = [r for r in rows if r['side'] == s and not np.isnan(r[m])]
        out[s] = lab.summarize([r[m] for r in x], [r['day'] for r in x])
    if out[1]['n'] < 20 or out[-1]['n'] < 20: return None
    return (out[1]['mean_bp'] + out[-1]['mean_bp']) / 2, out[1], out[-1]
def rep(name, rows, m='f30'):
    b = bal(rows, m)
    if b is None: return
    print('  %-28s bal=%+6.2fbp | BUY n=%5d %+6.2f t%+4.1f | SELL n=%5d %+6.2f t%+4.1f' % (name, b[0], b[1]['n'], b[1]['mean_bp'], b[1]['t_day'], b[2]['n'], b[2]['mean_bp'], b[2]['t_day']))
print(seg); rep('all', T); rep('all trade', T, 'trade'); rep('all f60', T, 'f60'); rep('all fclose', T, 'fclose')
for nm, fn in (('bos only', lambda r: r['bos'] and not r['fvg'] and not r['sweep']), ('fvg only', lambda r: r['fvg'] and not r['bos']),
               ('bos+fvg', lambda r: r['bos'] and r['fvg']), ('sweep', lambda r: r['sweep']), ('vol tier', lambda r: r['tier'] == 'vol')):
    rep(nm, [r for r in T if fn(r)])
def q(key, k=5):
    v = np.array([r[key] for r in T], float); ok = ~np.isnan(v)
    qs = np.quantile(v[ok], np.linspace(0, 1, k + 1)); print(key.upper())
    for j in range(k):
        rep('%s q%d [%.3g,%.3g)' % (key, j + 1, qs[j], qs[j + 1]), [r for r, x in zip(T, v) if qs[j] <= x < qs[j + 1] or (j == k - 1 and x == qs[-1])])
for key in ('tpb', 'rsis', 'ext', 'vr', 'dv', 'ema', 'r15', 'rday', 'mday', 'm15', 'atrbp', 'stopdist', 't'):
    q(key)
