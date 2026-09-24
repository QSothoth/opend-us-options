import sys, types, importlib.util, random
stub = types.ModuleType('futu'); stub.AuType = stub.KLType = stub.SubType = type('X', (), {'K_1M': None, 'NONE': None})
stub.OpenQuoteContext = object; stub.RET_OK = 0; sys.modules['futu'] = stub
spec = importlib.util.spec_from_file_location('w', __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..', '..', '..', 'watch', 'smc_watch.py')); w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
import lab, feat
from gran import ticks_per_bar
d = lab.load(); keys = sorted(d); random.seed(7); bad = 0; tot = 0
for k in random.sample(keys, 60):
    t, a = d[k]
    bars = [('2026-01-01 %02d:%02d:00' % (x // 60, x % 60),) + tuple(r) for x, r in zip(t, a)]
    ms, _ = w.marks(bars)
    ref = [(m['i'], 1 if m['side'] == 'BUY' else -1, m['tier']) for m in ms]
    f = feat.build(t, a); tpb = ticks_per_bar(f)
    mine = [x for x in feat.watcher_marks(f) if tpb[x[0]] >= 2.0]
    tot += len(ref)
    if ref != mine: bad += 1; print(k, len(ref), len(mine))
print('mismatch days', bad, 'marks compared', tot)
