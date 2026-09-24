import numpy as np
def zrel(f, L, mk='hsi'):
    c = f['c']; m = f[mk]; n = len(c)
    out = np.full(n, np.nan)
    with np.errstate(all='ignore'):
        out[L:] = (c[L:] / c[:-L] - m[L:] / m[:-L]) / (f['atr'][L:] / c[L:])
    return out
def fade(f, g, thr5=5, rsi=65, thr15=None, mk='hsi'):
    z5 = zrel(f, 5, mk)
    with np.errstate(invalid='ignore'):
        L = z5 < -thr5; S = z5 > thr5
        if thr15:
            z15 = zrel(f, 15, mk); L |= z15 < -thr15; S |= z15 > thr15
        if rsi: L &= f['rsi'] < 100 - rsi; S &= f['rsi'] > rsi
    return L, S
def fade_mkt(f, g, thr5=5, rsi=65, mk='hsi'):
    L, S = fade(f, g, thr5=thr5, rsi=rsi, mk=mk)
    m = f[mk]; tr = m / m[1] - 1
    return L & (tr > 0), S & (tr < 0)
