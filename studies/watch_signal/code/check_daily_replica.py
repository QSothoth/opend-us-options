import sys, types, importlib.util, random, os, csv, gzip
stub = types.ModuleType('futu'); stub.AuType = stub.KLType = stub.SubType = type('X', (), {'K_1M': None, 'K_DAY': None, 'NONE': None, 'QFQ': None})
stub.OpenQuoteContext = object; stub.RET_OK = 0; sys.modules['futu'] = stub
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('w', os.path.join(here, '..', '..', '..', 'watch', 'smc_watch.py')); w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
import lab, daily
from collections import defaultdict
DF = daily.features(daily.load_daily())
rows = defaultdict(list)
for r in csv.DictReader(gzip.open('day_2024-06_2026-09.csv.gz', 'rt')):
    rows[r['code']].append((r['time_key'][:10], float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['volume'] or 0)))
d = lab.load('hk1m_long.csv.gz', 'days.pkl'); keys = [k for k in sorted(d) if k[1] >= '2026-08-17' and (k[0], k[1]) in DF]
random.seed(3); bad = tot = 0
for k in random.sample(keys, 150):
    t, a = d[k]
    bars = [('%s %02d:%02d:00' % (k[1], x // 60, x % 60),) + tuple(r) for x, r in zip(t, a)]
    ms, _ = w.marks(bars)
    ms = w.with_daily(ms, w.daily_context(rows[k[0]], k[1]))
    g = DF[k]
    for m in ms:
        s = 1 if m['side'] == 'BUY' else -1
        pdlevel = (m['close'] > g['pdh']) if s > 0 else (m['close'] < g['pdl'])
        ref = (g['ret5d'] * s <= 0) and not pdlevel
        tot += 1; bad += (ref != m['daily'])
print('marks compared', tot, 'mismatches', bad)
