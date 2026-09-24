import numpy as np, sig2
def ticks_per_bar(f):
    """Causal: session-to-date mean bar range (h-l) in ticks, auction bar excluded."""
    if '_tpb' in f: return f['_tpb']
    tk = np.array([sig2.hk_tick(p) for p in f['c']])
    r = (f['h'] - f['l']) / tk; r[0] = 0
    n = len(r); out = np.cumsum(r) / np.maximum(np.arange(n), 1)
    f['_tpb'] = out; return out
