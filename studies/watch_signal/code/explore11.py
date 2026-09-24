import numpy as np, lab, sig, sig2, idx, feat
from collections import defaultdict
from ev import ctx, emit, add_delayed
from common import feats, split
F = feats(); add_delayed(F); idx.attach(F, 'HK.800000', 'hsi')
def ratio(f): return np.nanmedian(f['atr'][25:] / f['c'][25:]) * 1e4 / sig2.tick_bp(f)
RB = {k: ratio(f) for k, f in F.items()}
edges = [0, 1.5, 3, 6, 1e9]
def b(x):
    for i in range(4):
        if x < edges[i + 1]: return '%g-%g' % (edges[i], edges[i + 1])
for c in ('HK.02513', 'HK.00100', 'HK.09988', 'HK.01810', 'HK.00700', 'HK.03690'):
    v = [RB[k] for k in RB if k[0] == c]; print(c, 'atr/tick median %.2f' % np.median(v))
nsd = defaultdict(int)
for k in F:
    if split(k) == (__import__('sys').argv[1] if len(__import__('sys').argv) > 1 else 'train'): nsd[b(RB[k])] += 1
print(dict(nsd))
def ev(name, rule, which=__import__('sys').argv[1] if len(__import__('sys').argv) > 1 else 'train'):
    R = defaultdict(lambda: ([], []))
    for k, f in F.items():
        if split(k) != which: continue
        ev_ = rule(f)
        for i, s in ev_:
            R[b(RB[k])][0].append(s * f['d1f30'][i]); R[b(RB[k])][1].append(k[1])
    print(name)
    for bb in sorted(R, key=lambda x: float(x.split('-')[0])):
        s = lab.summarize(*R[bb])
        print('   ratio %-8s %5.2f/sd mean=%+6.2fbp hit=%5.1f%% payoff=%.2f t=%+5.1f' % (bb, len(R[bb][0]) / nsd[bb], s['mean_bp'], 100 * s['hit'], s['payoff'], s['t_day']))
ev('current', lambda f: [(i, s) for i, s, t in feat.watcher_marks(f)])
ev('W1-C2 atr zrel5>6 rsi60', lambda f: emit(*sig.fade(f, ctx(f), thr5=6, rsi=60), len(f['c'])))
ev('W1-C2 atr zrel5>3 rsi60', lambda f: emit(*sig.fade(f, ctx(f), thr5=3, rsi=60), len(f['c'])))
ev('rsi 25/75', lambda f: emit(f['rsi'] < 25, f['rsi'] > 75, len(f['c'])))
