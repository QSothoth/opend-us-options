import sys, types, importlib.util, random
stub = types.ModuleType('futu'); stub.AuType = stub.KLType = stub.SubType = type('X', (), {'K_1M': None, 'NONE': None})
stub.OpenQuoteContext = object; stub.RET_OK = 0; sys.modules['futu'] = stub
spec = importlib.util.spec_from_file_location('w', __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..', '..', '..', 'watch', 'smc_watch.py')); w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
import lab, feat
d = lab.load(); keys = sorted(d); random.seed(1)
bad = 0
for k in random.sample(keys, 40):
    t, a = d[k]
    bars = [('2026-01-01 %02d:%02d:00' % (x // 60, x % 60),) + tuple(r) for x, r in zip(t, a)]
    ms, _ = w.marks(bars)
    ref = [(m['i'], 1 if m['side'] == 'BUY' else -1, m['tier']) for m in ms]
    mine = feat.watcher_marks(feat.build(t, a))
    if ref != mine:
        bad += 1; print(k, len(ref), len(mine), set(ref) ^ set(mine))
print('mismatch days', bad)
