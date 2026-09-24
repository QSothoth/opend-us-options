import csv, gzip, numpy as np
from collections import defaultdict
def load_idx(path='idx1m.csv.gz'):
    raw = defaultdict(dict)
    with gzip.open(path, 'rt') as fh:
        for r in csv.DictReader(fh):
            t = r['time_key']; m = int(t[11:13]) * 60 + int(t[14:16])
            raw[(r['code'], t[:10])][m] = float(r['close'])
    return raw
def attach(F, code, name):
    raw = load_idx()
    miss = 0
    for (c, day), f in F.items():
        s = raw.get((code, day))
        if not s: f[name] = np.full(len(f['c']), np.nan); miss += 1; continue
        arr = np.array([s.get(int(m), np.nan) for m in f['t']])
        # forward fill
        for i in range(1, len(arr)):
            if np.isnan(arr[i]): arr[i] = arr[i - 1]
        f[name] = arr
    return miss
