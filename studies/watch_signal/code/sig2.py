import numpy as np
BANDS = [(0.25, 0.001), (0.5, 0.005), (10, 0.01), (20, 0.02), (100, 0.05), (200, 0.1), (500, 0.2),
         (1000, 0.5), (2000, 1.0), (5000, 2.0), (9995, 5.0)]
def hk_tick(p):
    for hi, t in BANDS:
        if p < hi: return t
    return 5.0
def tick_bp(f):
    if '_tick' not in f: f['_tick'] = hk_tick(f['c'][25]) / f['c'][25] * 1e4
    return f['_tick']
def zs(f, L=5, mk='hsi', minbars=20):
    """relative L-bar move over session-to-date RMS of 1-bar relative moves * sqrt(L)."""
    key = '_zs%d%s' % (L, mk)
    if key in f: return f[key]
    c = f['c']; m = f[mk]; n = len(c)
    r1 = np.zeros(n); r1[2:] = np.log(c[2:] / c[1:-1]) - np.log(m[2:] / m[1:-1])  # skip auction->first minute
    cs = np.cumsum(r1 ** 2); cnt = np.maximum(np.arange(n) - 1, 1)
    sig1 = np.sqrt(cs / cnt)
    rel = np.full(n, np.nan); rel[L:] = np.log(c[L:] / c[:-L]) - np.log(m[L:] / m[:-L])
    with np.errstate(all='ignore'):
        z = rel / (sig1 * np.sqrt(L))
    z[:minbars] = np.nan
    f[key] = z; return z
def fade(f, g, k=2.5, rsi=60, L=5, maxtick=None, mk='hsi'):
    z = zs(f, L, mk)
    with np.errstate(invalid='ignore'):
        Lo = z < -k; Sh = z > k
        if rsi: Lo &= f['rsi'] < 100 - rsi; Sh &= f['rsi'] > rsi
    if maxtick is not None and tick_bp(f) > maxtick:
        Lo &= False; Sh &= False
    return Lo, Sh
