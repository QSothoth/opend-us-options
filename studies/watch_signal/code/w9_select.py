"""W9 selection on 2025-12-01..2026-08-14 only (validation data not downloaded yet)."""
import pickle, daily
from balstat import bal, side_mean
DF = daily.features(daily.load_daily())
R, NSD = pickle.load(open('w7_events.pkl', 'rb'))
tr = [r for r in R if r['seg'] == 'train' and r['tpb'] >= 2]
V = {'V1': [r for r in tr if r['cand'] == 'R5'], 'V2': [r for r in tr if r['cand'] == 'R4'],
     'V3': [r for r in tr if r['cand'] == 'R5' and DF[(r['code'], r['day'])]['ret5d'] * r['side'] <= 0]}
res = {}
for k, x in V.items():
    c, a = bal(x, 'fclose'), bal(x, 'f30')
    res[k] = c[1]
    print('%s n=%6d dens=%.2f/sd | close bal %+6.2f t%+4.1f | f30 bal %+6.2f t%+4.1f | BUY %+6.2f SELL %+6.2f'
          % (k, len(x), len(x) / NSD[('train', 'g')], c[0], c[1], a[0], a[1], side_mean(x, 1, 'fclose')[0], side_mean(x, -1, 'fclose')[0]))
print('PICK', max(res, key=res.get))
