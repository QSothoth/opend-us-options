import numpy as np, lab, sig, sig2, idx, pickle
from collections import defaultdict
from ev import ctx, emit, add_delayed
from common import feats, split
F = feats(); idx.attach(F, 'HK.800000', 'hsi')
RC = pickle.load(open('ratio_by_code.pkl', 'rb'))
out = defaultdict(lambda: ([], []))
for k, f in F.items():
    if split(k) != 'train': continue
    grp = 'granular' if RC[k[0]] >= 1.5 else 'tickbound'
    c = f['c']; n = len(c); tb = sig2.tick_bp(f)
    for i, s in emit(*sig.fade(f, ctx(f), thr5=6, rsi=60), n):
        for lag in (0, 1, 2, 5, 10):
            if i + lag + 30 < n:
                r = s * (c[i + lag + 30] / c[i + lag] - 1)
                out[(grp, lag, 'bp')][0].append(r); out[(grp, lag, 'bp')][1].append(k[1])
                out[(grp, lag, 'tk')][0].append(r * 1e4 / tb); out[(grp, lag, 'tk')][1].append(k[1])
for key in sorted(out):
    s = lab.summarize(*out[key])
    unit = 'bp' if key[2] == 'bp' else 'ticks(x1e-4)'
    print('%-9s lag%-2d %-3s n=%6d mean=%+7.3f %s hit=%5.1f%% t=%+5.1f' % (key[0], key[1], key[2], s['n'], s['mean_bp'] if key[2] == 'bp' else s['mean_bp'] / 1e4, 'bp' if key[2] == 'bp' else 'tick', 100 * s['hit'], s['t_day']))
