import numpy as np, lab, feat, sys
from collections import defaultdict
from ev import add_delayed, emit
from common import feats, split
from gran import ticks_per_bar
F = feats(); add_delayed(F)
which = sys.argv[1]
edges = [0, 1, 1.5, 2, 3, 5, 1e9]
def bk(x):
    for j in range(len(edges) - 1):
        if x < edges[j + 1]: return j
def ev(name, rule, h='d1f30'):
    R = defaultdict(lambda: ([], []))
    for k, f in F.items():
        if split(k) != which: continue
        tpb = ticks_per_bar(f)
        for i, s in rule(f):
            R[bk(tpb[i])][0].append(s * f[h][i]); R[bk(tpb[i])][1].append(k[1])
    print(name, h)
    for j in sorted(R):
        st = lab.summarize(*R[j])
        if st['n'] >= 5: print('   tpb %4g-%-4g n=%6d mean=%+6.2fbp hit=%5.1f%% payoff=%.2f t=%+5.1f' % (edges[j], edges[j + 1], st['n'], st['mean_bp'], 100 * st['hit'], st['payoff'], st['t_day']))
cur = lambda f: [(i, s) for i, s, t in feat.watcher_marks(f)]
ev('current', cur); ev('current', cur, 'd1fclose')
def mom(f, L=30, k=3):
    c = f['c']; r = np.full(len(c), np.nan)
    with np.errstate(all='ignore'):
        r[L:] = np.log(c[L:] / c[:-L]) / (f['atr'][L:] / c[L:]); return emit(r > k, r < -k, len(c))
ev('mom30>3atr', mom)
ev('vwap side', lambda f: emit(f['c'] > f['vwap'], f['c'] < f['vwap'], len(f['c'])))
