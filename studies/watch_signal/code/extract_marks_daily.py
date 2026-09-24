"""All shown marks (incl. ~) with intraday attrs + daily pre-open features. Streams per stock-day."""
import numpy as np, pickle, lab, feat, daily
from gran import ticks_per_bar
DF = daily.features(daily.load_daily())
def rows_for(days, seg):
    out = []
    for (code, day), (t, a) in days.items():
        if code.startswith('HK.8'): continue
        g = DF.get((code, day))
        if g is None: continue
        f = feat.build(t, a); c = f['c']; n = len(c); tpb = ticks_per_bar(f)
        gap = c[0] / g['pdc'] - 1                # 09:30 auction print vs previous close
        for i, s, tier in feat.watcher_marks(f):
            def fr(L):
                j = i + 1 + L
                return s * (c[j] / c[i + 1] - 1) if j <= n - 1 else np.nan
            out.append({'seg': seg, 'code': code, 'day': day, 'i': i, 't': int(t[i]), 'side': s,
                        'tpb': tpb[i], 'f30': fr(30), 'f60': fr(60),
                        'fclose': s * (c[-1] / c[i + 1] - 1) if i + 1 < n else np.nan,
                        'align': g['stage'] * s, 'rvol_d1': g['rvol_d1'], 'contract5': g['contract5'],
                        'udvol10': g['udvol10'] * s, 'ret5d': g['ret5d'] * s, 'ret20d': g['ret20d'] * s,
                        'ema20slope': g['ema20slope'] * s, 'gap': gap * s, 'atr20p': g['atr20p'],
                        'dist20': (g['dist20h'] if s > 0 else -g['dist20l']),
                        'pdlevel': (c[i] > g['pdh']) if s > 0 else (c[i] < g['pdl']),
                        'dayret': s * (c[i] / c[0] - 1)})
    return out
if __name__ == '__main__':
    R = []
    for path, cache, lo, hi, seg in (('old1m.csv.gz', 'days_old.pkl', '2025-12-01', '2026-05-29', 'train'),
                                     ('hk1m_long.csv.gz', 'days.pkl', '2026-06-01', '2026-08-14', 'train'),
                                     ('hk1m_long.csv.gz', 'days.pkl', '2026-08-17', '2026-09-21', 'valid'),
                                     ('new1m.csv.gz', 'days_new.pkl', '2026-09-22', '2026-09-23', 'valid')):
        d = lab.load(path, cache); d = {k: v for k, v in d.items() if lo <= k[1] <= hi}
        R += rows_for(d, seg); print(path, lo, len(R), flush=True)
    pickle.dump(R, open('marks_daily.pkl', 'wb'))
