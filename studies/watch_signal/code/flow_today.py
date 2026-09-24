"""One-day test of the optional flow score and W4 candidates. NOT evidence: one session."""
# usage: python flow_today.py ../../../data/watch-hk-flow-2026-09-24-partial
import sys, types, importlib.util, csv, gzip, numpy as np
from collections import defaultdict
stub = types.ModuleType('futu'); stub.AuType = stub.KLType = stub.SubType = type('X', (), {'K_1M': None, 'NONE': None})
stub.OpenQuoteContext = object; stub.RET_OK = 0; sys.modules['futu'] = stub
spec = importlib.util.spec_from_file_location('w', __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..', '..', '..', 'watch', 'smc_watch.py')); w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
day = '2026-09-24'
bars, flow = defaultdict(list), defaultdict(dict)
for r in csv.DictReader(gzip.open(__import__('sys').argv[1] + '/bars_1m.csv.gz', 'rt')):
    if r['time_key'][11:16] < '16:00':
        bars[r['code']].append((r['time_key'], float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r['volume'])))
for r in csv.DictReader(gzip.open(__import__('sys').argv[1] + '/capital_flow_1m.csv.gz', 'rt')):
    flow[r['code']][r['time'][11:16]] = float(r['in_flow'])
H = 30
def fwd(b, i):  # next-bar entry, H bars later
    return b[i + 1 + H][4] / b[i + 1][4] - 1 if i + 1 + H < len(b) else np.nan
rows = []
for code, b in bars.items():
    b.sort()
    if code == 'HK.800000' or len(b) < 60 or not flow.get(code): continue
    ms, _ = w.marks(b)
    ms = w.with_flow(ms, b, flow[code])
    for m in ms:
        s = 1 if m['side'] == 'BUY' else -1
        rows.append((code, m['side'], m['coarse'], m['flow'], s * fwd(b, m['i'])))
def show(name, rs):
    x = np.array([r[4] for r in rs if not np.isnan(r[4])])
    if len(x) < 10: print('  %-34s n=%d' % (name, len(x))); return
    by = {sd: np.array([r[4] for r in rs if r[1] == sd and not np.isnan(r[4])]) for sd in ('BUY', 'SELL')}
    bal = np.mean([by[k].mean() for k in by if len(by[k])]) * 1e4
    nz = x[x != 0]
    print('  %-34s n=%4d mean=%+6.2fbp bal=%+6.2fbp hit=%4.1f%% codes=%d' % (name, len(x), x.mean() * 1e4, bal, 100 * (nz > 0).mean(), len({r[0] for r in rs})))
print('marks with flow score, d1f30, day %s (partial session)' % day)
sc = [r for r in rows if r[3] is not None]
show('all marks', rows); show('scored', sc)
show('f > 0 (flow agrees)', [r for r in sc if r[3] > 0]); show('f <= 0', [r for r in sc if r[3] <= 0])
show('f > 0.2', [r for r in sc if r[3] > 0.2]); show('f < -0.2', [r for r in sc if r[3] < -0.2])
for c in (False, True):
    t = 'coarse ~' if c else 'granular'
    show(t + ' f>0', [r for r in sc if r[2] == c and r[3] > 0]); show(t + ' f<=0', [r for r in sc if r[2] == c and r[3] <= 0])
# W4 standalone candidates on every bar (F1/F2 need size buckets; F1 and F4 only here)
print('W4 F1 standalone: imb15>0.2 -> BUY, <-0.2 -> SELL, dedup 15, warmup 25')
f1 = []
for code, b in bars.items():
    if code == 'HK.800000' or len(b) < 60 or not flow.get(code): continue
    last = {1: -99, -1: -99}
    for i in range(25, len(b)):
        sc_ = w.flow_score(b, flow[code], i, 'BUY')
        if sc_ is None: continue
        s = 1 if sc_ > 0.2 else (-1 if sc_ < -0.2 else 0)
        if s and i - last[s] >= 15:
            last[s] = i; f1.append((code, 'BUY' if s > 0 else 'SELL', None, None, s * fwd(b, i)))
show('F1 flow follow', f1); show('F4 flow fade', [(a, 'SELL' if s == 'BUY' else 'BUY', c, d, -x) for a, s, c, d, x in f1])
