"""Daily features known before the session opens (only days strictly before `day`)."""
import csv, gzip, numpy as np
from collections import defaultdict
def load_daily(path='day_2024-06_2026-09.csv.gz'):
    raw = defaultdict(list)
    for r in csv.DictReader(gzip.open(path, 'rt')):
        raw[r['code']].append((r['time_key'][:10], float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['volume'] or 0)))
    out = {}
    for code, rows in raw.items():
        rows.sort(); d = [x[0] for x in rows]; a = np.array([x[1:] for x in rows])
        out[code] = (d, a)
    return out
def ema(x, n):
    k = 2 / (n + 1); o = np.full(len(x), np.nan)
    if len(x) < n: return o
    o[n - 1] = x[:n].mean()
    for i in range(n, len(x)): o[i] = x[i] * k + o[i - 1] * (1 - k)
    return o
def features(D):
    """{(code, day): feats} where feats use rows < day (i.e. through the previous session)."""
    F = {}
    for code, (days, a) in D.items():
        o, h, l, c, v = a.T; n = len(c)
        e10, e20 = ema(c, 10), ema(c, 20)
        s50 = np.array([c[max(0, i - 49):i + 1].mean() if i >= 49 else np.nan for i in range(n)])
        tr = np.maximum(h[1:], c[:-1]) - np.minimum(l[1:], c[:-1]); tr = np.concatenate([[h[0] - l[0]], tr])
        for j in range(1, n):          # features for session days[j], computed from rows 0..j-1
            p = j - 1
            if p < 50: continue
            atr20 = tr[p - 19:p + 1].mean()
            up = c[p] > e10[p] > e20[p] > s50[p]; dn = c[p] < e10[p] < e20[p] < s50[p]
            ud = np.sign(np.diff(c[p - 10:p + 1])); vv = v[p - 9:p + 1]
            upv, dnv = vv[ud > 0].sum(), vv[ud < 0].sum()
            F[(code, days[j])] = {
                'stage': 1 if up else (-1 if dn else 0),
                'rvol_d1': v[p] / max(v[p - 20:p].mean(), 1),
                'contract5': (h[p - 4:p + 1].max() - l[p - 4:p + 1].min()) / atr20 if atr20 > 0 else np.nan,
                'udvol10': np.log((upv + 1) / (dnv + 1)),
                'ret5d': c[p] / c[p - 5] - 1, 'ret20d': c[p] / c[p - 20] - 1,
                'dist20h': c[p] / h[p - 19:p + 1].max() - 1, 'dist20l': c[p] / l[p - 19:p + 1].min() - 1,
                'pdh': h[p], 'pdl': l[p], 'pdc': c[p], 'atr20p': atr20 / c[p],
                'ema20slope': e20[p] / e20[p - 5] - 1}
    return F
