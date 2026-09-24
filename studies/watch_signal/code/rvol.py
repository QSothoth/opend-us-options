import numpy as np
from collections import defaultdict
def attach_rvol(F, lookback=5):
    """rvol[i] = cumvol[:i+1] / mean over prior `lookback` sessions of cumvol[:i+1] (same bar index)."""
    by = defaultdict(list)
    for (c, d) in F: by[c].append(d)
    for c, ds in by.items():
        ds.sort(); hist = []
        for d in ds:
            f = F[(c, d)]; cv = np.cumsum(f['v'])
            if len(hist) >= lookback:
                base = np.mean(np.vstack([h[:len(cv)] if len(h) >= len(cv) else np.pad(h, (0, len(cv) - len(h)), mode='edge') for h in hist[-lookback:]]), 0)
                with np.errstate(all='ignore'): f['rvol'] = cv / base
            else:
                f['rvol'] = np.full(len(cv), np.nan)
            hist.append(cv)
