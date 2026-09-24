"""Per stock-day feature arrays, all causal (index i uses bars <= i)."""
import numpy as np
import lab

def swings(h, l, left=3, look=24):
    """Replicates watcher.last_swing(highs[-24:], lows[-24:]) at each i: latest pivot
    confirmed on both sides inside the trailing 24-bar window."""
    n = len(h)
    ish = np.zeros(n, bool); isl = np.zeros(n, bool)
    for j in range(left, n - left):
        if h[j] == h[j-left:j+left+1].max(): ish[j] = True
        if l[j] == l[j-left:j+left+1].min(): isl[j] = True
    sh = np.full(n, np.nan); sl = np.full(n, np.nan)
    for i in range(n):
        lo = max(0, i + 1 - look)
        # pivot j valid if j+left <= i and j-left >= lo
        for j in range(i - left, lo + left - 1, -1):
            if ish[j]: sh[i] = h[j]; break
        for j in range(i - left, lo + left - 1, -1):
            if isl[j]: sl[i] = l[j]; break
    return sh, sl

def build(t, a, drop_auction=False):
    o, h, l, c, v = a.T
    n = len(c)
    f = {'t': t, 'o': o, 'h': h, 'l': l, 'c': c, 'v': v}
    tp = (h + l + c) / 3
    cv = np.cumsum(v); f['vwap'] = np.where(cv > 0, np.cumsum(tp * v) / np.maximum(cv, 1e-9), np.nan)
    f['ema9'] = lab.ema(c, 9); f['ema21'] = lab.ema(c, 21); f['rsi'] = lab.rsi(c)
    rng = h - l
    cs = np.concatenate([[0], np.cumsum(rng)])
    idx = np.arange(n)
    lo14 = np.maximum(0, idx - 13)
    f['atr'] = (cs[idx + 1] - cs[lo14]) / (idx + 1 - lo14)
    csv_ = np.concatenate([[0], np.cumsum(v)])
    lo20 = np.maximum(0, idx - 19)
    avgv = (csv_[idx + 1] - csv_[lo20]) / (idx + 1 - lo20)
    f['vr'] = np.where(avgv > 0, v / np.maximum(avgv, 1e-9), 0)
    f['dhi'] = np.maximum.accumulate(h); f['dlo'] = np.minimum.accumulate(l)
    span = f['dhi'] - f['dlo']
    f['pos'] = np.where(span > 0, (c - f['dlo']) / np.where(span > 0, span, 1), 0.5)
    f['sh'], f['sl'] = swings(h, l)
    for hz in (5, 15, 30, 60):
        f['f%d' % hz] = lab.fwd(c, hz)
    f['fclose'] = c[-1] / c - 1
    return f

def watcher_marks(f, warm=25, dedup=15):
    """Vectorised replica of watcher.marks(rule='repeat15')."""
    c, h, l, atr = f['c'], f['h'], f['l'], f['atr']
    n = len(c)
    bull_bos = c > f['sh']; bear_bos = c < f['sl']
    gu = np.full(n, -np.inf); gd = np.full(n, -np.inf)
    gu[2:] = l[2:] - h[:-2]; gd[2:] = l[:-2] - h[2:]
    fvg_u = gu > 0.25 * atr; fvg_d = (~fvg_u) & (gd > 0.25 * atr)
    sw_b = (l < f['sl']) & (f['sl'] <= c); sw_s = (h > f['sh']) & (f['sh'] >= c)
    bull = (bull_bos | fvg_u | sw_b) & (atr > 0); bear = (bear_bos | fvg_d | sw_s) & (atr > 0)
    lb = (c > f['vwap']) & (f['ema9'] > f['ema21']) & (f['rsi'] >= 50)
    sb = (c < f['vwap']) & (f['ema9'] < f['ema21']) & (f['rsi'] <= 50)
    out = []
    last = {}
    for i in range(warm, n):
        if np.isnan(f['rsi'][i]) or np.isnan(f['ema21'][i]):
            continue
        side = None
        if lb[i] and bull[i]:
            side = 1; zone = f['pos'][i] <= 0.5
        elif sb[i] and bear[i]:
            side = -1; zone = f['pos'][i] >= 0.5
        if side is None: continue
        tier = 'vol' if (f['vr'][i] >= 1.5 and zone) else 'plain'
        if i - last.get((side, tier), -10**9) < dedup: continue
        last[(side, tier)] = i
        out.append((i, side, tier))
    return out
