import numpy as np, lab
from common import feats, split
def lagret(x, L):
    out = np.full(len(x), np.nan); out[L:] = x[L:] / x[:-L] - 1; return out
def ctx(f):
    if '_ctx' in f: return f['_ctx']
    c = f['c']; atr = f['atr']
    with np.errstate(all='ignore'):
        g = dict(r5=lagret(c, 5), r15=lagret(c, 15), m5=lagret(f['mkt'], 5), m15=lagret(f['mkt'], 15),
                 dv=(c - f['vwap']) / atr, atrbp=atr / c)
        g['rel15'] = g['r15'] - g['m15']; g['rel5'] = g['r5'] - g['m5']
        g['z5'] = g['r5'] / g['atrbp']; g['z15'] = g['r15'] / g['atrbp']
        g['zrel15'] = g['rel15'] / g['atrbp']; g['zrel5'] = g['rel5'] / g['atrbp']
    f['_ctx'] = g; return g
def emit(long_, short_, n, warm=25, gap=15, stop=None):
    out = []; last = {1: -10**9, -1: -10**9}
    end = n if stop is None else stop
    for i in range(warm, end):
        s = 1 if long_[i] else (-1 if short_[i] else 0)
        if s and i - last[s] >= gap:
            last[s] = i; out.append((i, s))
    return out
def run(rule, which='train', hs=('f15', 'f30', 'fclose'), norm=False, warm=25, keys=None, **kw):
    F = feats(); add_delayed(F); R = {h: ([], []) for h in hs}; nsd = 0; nsig = 0
    for k, f in F.items():
        if which != 'all' and split(k) != which: continue
        if keys is not None and k not in keys: continue
        nsd += 1
        L, S = rule(f, ctx(f), **kw)
        for i, s in emit(L, S, len(f['c']), warm=warm):
            nsig += 1
            for h in hs:
                v = s * f[h][i]
                if norm: v = v / ctx(f)['atrbp'][i] / 1e4
                R[h][0].append(v); R[h][1].append(k[1])
    return nsig / max(nsd, 1), {h: lab.summarize(*R[h], label=h) for h in hs}
def show(name, res):
    dens, st = res
    print('%-34s %5.2f/sd | ' % (name, dens) + ' | '.join(
        '%s %+6.2fbp hit%4.1f%% po%.2f t%+5.1f' % (h, s.get('mean_bp', np.nan), 100 * s.get('hit', np.nan),
                                               s.get('payoff', np.nan), s.get('t_day', np.nan)) for h, s in st.items()), flush=True)
def add_delayed(F):
    """Entry at the NEXT bar's close (d1) or two bars later (d2): kills bid-ask bounce."""
    for f in F.values():
        if 'd1f15' in f: continue
        c = f['c']; n = len(c)
        for lag in (1, 2):
            for h in (15, 30):
                out = np.full(n, np.nan)
                if n > lag + h: out[:n - lag - h] = c[lag + h:] / c[lag:n - h] - 1
                f['d%df%d' % (lag, h)] = out
            out = np.full(n, np.nan); out[:n - lag] = c[-1] / c[lag:] - 1
            f['d%dfclose' % lag] = out
        # entry at next bar open
        out = np.full(n, np.nan); o = f['o']
        if n > 31: out[:n - 31] = c[31:] / o[1:n - 30] - 1
        f['o1f30'] = out
