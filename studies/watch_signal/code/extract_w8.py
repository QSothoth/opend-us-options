"""All marks with W6 daily fields + W8 intraday tag fields. Streams per stock-day."""
import numpy as np, pickle, lab, feat, daily
from gran import ticks_per_bar
DF = daily.features(daily.load_daily())
def squeeze_since(c, atr, period=20, band=2.0, chan=1.5):
    n = len(c); out = np.full(n, 10 ** 6); last = None
    for i in range(n):
        if i + 1 >= period:
            w = c[i + 1 - period:i + 1]
            if band * w.std() <= chan * atr[i]:
                last = i
        if last is not None:
            out[i] = i - last
    return out
def rows_for(days, seg):
    out = []
    for (code, day), (t, a) in days.items():
        if code.startswith('HK.8'): continue
        g = DF.get((code, day))
        if g is None: continue
        f = feat.build(t, a); c = f['c']; n = len(c); tpb = ticks_per_bar(f)
        sq = squeeze_since(c, f['atr'])
        path = np.concatenate([[0], np.cumsum(np.abs(np.diff(c)))])
        for i, s, tier in feat.watcher_marks(f):
            atr = f['atr'][i]
            er = abs(c[i] - c[1]) / (path[i] - path[1]) if i > 1 and path[i] > path[1] else np.nan
            pdlevel = (c[i] > g['pdh']) if s > 0 else (c[i] < g['pdl'])
            out.append({'seg': seg, 'code': code, 'day': day, 'i': i, 'side': s, 'tpb': tpb[i],
                        'f30': s * (c[i + 31] / c[i + 1] - 1) if i + 31 <= n - 1 else np.nan,
                        'fclose': s * (c[-1] / c[i + 1] - 1) if i + 1 <= n - 1 else np.nan,
                        'daily': (g['ret5d'] * s <= 0) and not pdlevel,
                        'T1': (c[i] - f['vwap'][i]) * s <= 2 * atr,
                        'T2': sq[i] < 30,
                        'T3': (er >= 0.1) if er == er else False,
                        'vwap_atr': (c[i] - f['vwap'][i]) * s / atr if atr > 0 else np.nan, 'er': er})
    return out
if __name__ == '__main__':
    R = []
    for path, cache, lo, hi, seg in (('old1m.csv.gz', 'days_old.pkl', '2025-12-01', '2026-05-29', 'train'),
                                     ('hk1m_long.csv.gz', 'days.pkl', '2026-06-01', '2026-08-14', 'train'),
                                     ('hk1m_long.csv.gz', 'days.pkl', '2026-08-17', '2026-09-21', 'valid'),
                                     ('new1m.csv.gz', 'days_new.pkl', '2026-09-22', '2026-09-23', 'valid')):
        d = lab.load(path, cache); d = {k: v for k, v in d.items() if lo <= k[1] <= hi}
        R += rows_for(d, seg); print(path, lo, len(R), flush=True)
    pickle.dump(R, open('marks_w8.pkl', 'wb'))
