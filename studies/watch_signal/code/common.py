import pickle, numpy as np
from collections import defaultdict
VALID_START = '2026-08-17'
_F = None
def feats():
    global _F
    if _F is None:
        _F = pickle.load(open('feats.pkl', 'rb'))
        add_market(_F)
    return _F
def add_market(F):
    """Equal-weight cross-section of 1m log returns per day -> market index level."""
    byday = defaultdict(list)
    for (code, day), f in F.items():
        byday[day].append(f)
    for day, fs in byday.items():
        r = np.nanmean(np.vstack([np.concatenate([[0], np.diff(np.log(f['c']))]) for f in fs]), axis=0)
        lvl = np.exp(np.cumsum(r))
        for f in fs:
            f['mkt'] = lvl
def split(k): return 'valid' if k[1] >= VALID_START else 'train'
