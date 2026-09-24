"""One row per shown mark (tpb >= 2): attributes at the mark bar + outcomes. Streams per stock-day."""
import numpy as np, pickle, sys, lab, feat, idx
from gran import ticks_per_bar
from w2_eval import trade
def rows_for(days, hsi_raw, seg):
    out = []
    for (code, day), (t, a) in days.items():
        if code.startswith('HK.8'): continue
        f = feat.build(t, a); c = f['c']; n = len(c); tpb = ticks_per_bar(f)
        if tpb[25:].max(initial=0) < 2: continue
        s_idx = hsi_raw.get(('HK.800000', day))
        hs = np.array([s_idx.get(int(m), np.nan) for m in t]) if s_idx else np.full(n, np.nan)
        for i in range(1, n):
            if np.isnan(hs[i]): hs[i] = hs[i - 1]
        h, l, o, v = f['h'], f['l'], f['o'], f['v']
        gu = np.full(n, -np.inf); gd = np.full(n, -np.inf); gu[2:] = l[2:] - h[:-2]; gd[2:] = l[:-2] - h[2:]
        for i, s, tier in feat.watcher_marks(f):
            if tpb[i] < 2: continue
            sh, sl = f['sh'][i], f['sl'][i]
            bos = (c[i] > sh) if s > 0 else (c[i] < sl)
            fvg = (gu[i] > 0.25 * f['atr'][i]) if s > 0 else (gd[i] > 0.25 * f['atr'][i])
            sweep = ((l[i] < sl <= c[i]) if s > 0 else (h[i] > sh >= c[i]))
            def fr(L):
                j = i + 1 + L
                return s * (c[min(j, n - 1)] / c[i + 1] - 1) if i + 1 < n and j <= n - 1 else np.nan
            atrp = f['atr'][i] / c[i]
            r = {'seg': seg, 'code': code, 'day': day, 'i': i, 't': int(t[i]), 'side': s, 'tier': tier,
                 'tpb': tpb[i], 'bos': bool(bos), 'fvg': bool(fvg), 'sweep': bool(sweep),
                 'rsi': f['rsi'][i], 'vr': f['vr'][i], 'pos': f['pos'][i],
                 'dv': (c[i] - f['vwap'][i]) / f['atr'][i] * s, 'ema': (f['ema9'][i] - f['ema21'][i]) / f['atr'][i] * s,
                 'r15': s * (c[i] / c[max(i - 15, 1)] - 1) / atrp, 'rday': s * (c[i] / o[1] - 1) / atrp,
                 'mday': s * (hs[i] / hs[1] - 1) / atrp if not np.isnan(hs[1]) else np.nan,
                 'm15': s * (hs[i] / hs[max(i - 15, 1)] - 1) / atrp if not np.isnan(hs[1]) else np.nan,
                 'atrbp': atrp * 1e4, 'stopdist': (abs(c[i] - (sl if s > 0 else sh)) / f['atr'][i]) if not np.isnan(sl if s > 0 else sh) else np.nan,
                 'f15': fr(15), 'f30': fr(30), 'f60': fr(60), 'fclose': s * (c[-1] / c[i + 1] - 1) if i + 1 < n else np.nan,
                 'trade': trade(f, i, s, sl if s > 0 else sh)}
            out.append(r)
    return out
if __name__ == '__main__':
    raw_hsi = idx.load_idx('old1m.csv.gz')
    raw_hsi.update(idx.load_idx('idx1m.csv.gz'))
    R = []
    for path, cache, lo, hi, seg in (('old1m.csv.gz', 'days_old.pkl', '2025-12-01', '2026-05-29', 'train'),
                                     ('hk1m_long.csv.gz', 'days.pkl', '2026-06-01', '2026-08-14', 'train'),
                                     ('hk1m_long.csv.gz', 'days.pkl', '2026-08-17', '2026-09-21', 'valid'),
                                     ('new1m.csv.gz', 'days_new.pkl', '2026-09-22', '2026-09-23', 'valid')):
        d = lab.load(path, cache); d = {k: v for k, v in d.items() if lo <= k[1] <= hi}
        R += rows_for(d, raw_hsi, seg); print(path, lo, len(R), flush=True)
    pickle.dump(R, open('marks_granular.pkl', 'wb'))
