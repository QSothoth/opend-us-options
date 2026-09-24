import numpy as np, pickle, lab, feat, daily
from gran import ticks_per_bar
from balstat import bal, side_mean
DF = daily.features(daily.load_daily())
def rows_for(days, seg):
    out = []
    for (code, day), (t, a) in days.items():
        if code.startswith('HK.8'): continue
        g = DF.get((code, day))
        if g is None or g['ret5d'] == 0: continue
        f = feat.build(t, a); c = f['c']; tpb = ticks_per_bar(f)
        s = -1 if g['ret5d'] > 0 else 1
        out.append({'seg': seg, 'day': day, 'code': code, 'side': s, 'tpb': tpb[len(c) // 2], 'fclose': s * (c[-1] / c[1] - 1)})
    return out
R = []
for path, cache, lo, hi, seg in (('h2025_1m.csv.gz', 'days_h2025.pkl', '2025-06-02', '2025-11-28', 'new'),
                                 ('old1m.csv.gz', 'days_old.pkl', '2025-12-01', '2026-05-29', 'sel'),
                                 ('hk1m_long.csv.gz', 'days.pkl', '2026-06-01', '2026-08-14', 'sel'),
                                 ('hk1m_long.csv.gz', 'days.pkl', '2026-08-17', '2026-09-21', 'val'),
                                 ('new1m.csv.gz', 'days_new.pkl', '2026-09-22', '2026-09-23', 'val')):
    d = lab.load(path, cache); d = {k: v for k, v in d.items() if lo <= k[1] <= hi}
    R += rows_for(d, seg)
for gran in (True, False):
    print('==', 'granular' if gran else 'coarse ~')
    for name, segs in (('2025-06..11', {'new'}), ('selection', {'sel'}), ('validation', {'val'}), ('OUT-OF-SELECTION (new+val)', {'new', 'val'})):
        x = [r for r in R if r['seg'] in segs and (r['tpb'] >= 2) == gran]
        b = bal(x, 'fclose'); by, sy = side_mean(x, 1, 'fclose'), side_mean(x, -1, 'fclose')
        print('  %-28s n=%5d | bal %+6.2f t%+4.1f | BUY %+6.2f (n%d) SELL %+6.2f (n%d)' % (name, len(x), b[0], b[1], by[0], by[1], sy[0], sy[1]))
        if gran and name.startswith('OUT'):
            print('  gates', [b[0] > 0 and b[1] >= 2, by[0] > 0 and sy[0] > 0])
