"""Research harness for watch/ signals. numpy only for speed; not runtime code."""
import csv, gzip, pickle, os, sys
import numpy as np
from collections import defaultdict

S = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def load(path='hk1m_long.csv.gz', cache='days.pkl'):  # run with cwd = ../data
    if os.path.exists(cache) and os.path.getmtime(cache) > os.path.getmtime(path):
        with open(cache, 'rb') as fh:
            return pickle.load(fh)
    raw = defaultdict(list)
    with gzip.open(path, 'rt') as fh:
        for r in csv.DictReader(fh):
            t = r['time_key']
            raw[(r['code'], t[:10])].append((t[11:16], float(r['open']), float(r['high']),
                                            float(r['low']), float(r['close']), float(r['volume'] or 0)))
    days = {}
    for k, rows in raw.items():
        rows.sort()
        rows = [x for x in rows if x[0] < '16:00']
        if len(rows) < 200:
            continue
        t = np.array([int(x[0][:2]) * 60 + int(x[0][3:]) for x in rows], dtype=np.int32)
        a = np.array([x[1:] for x in rows], dtype=np.float64)
        days[k] = (t, a)
    with open(cache, 'wb') as fh:
        pickle.dump(days, fh, protocol=4)
    return days


def ema(x, n):
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    k = 2.0 / (n + 1)
    prev = x[:n].mean()
    out[n - 1] = prev
    for i in range(n, len(x)):
        prev = x[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(c, n=14):
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c)
    g, l = np.maximum(d, 0), np.maximum(-d, 0)
    ag, al = g[:n].mean(), l[:n].mean()
    out[n] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(c)):
        ag = (ag * (n - 1) + g[i - 1]) / n
        al = (al * (n - 1) + l[i - 1]) / n
        out[i] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def fwd(c, h):
    """close[i+h]/close[i]-1, nan past end of session."""
    out = np.full(len(c), np.nan)
    if len(c) > h:
        out[:-h] = c[h:] / c[:-h] - 1
    return out


def summarize(rets, days_of, label=''):
    """rets: signed returns (in signal direction). days_of: day key per signal for clustering."""
    r = np.asarray(rets, float)
    ok = ~np.isnan(r)
    r = r[ok]
    dk = np.asarray(days_of)[ok]
    n = len(r)
    if n < 5:
        return {'label': label, 'n': n}
    w = r[r > 0]; l = r[r <= 0]
    # day-clustered t
    by = defaultdict(list)
    for x, d in zip(r, dk):
        by[d].append(x)
    dm = np.array([np.sum(v) for v in by.values()])
    tc = r.mean() * n / (dm.std(ddof=1) * np.sqrt(len(dm))) if len(dm) > 2 and dm.std() > 0 else np.nan
    nz = r[r != 0]
    return {'label': label, 'n': n, 'win': (r > 0).mean(), 'hit': (nz > 0).mean() if len(nz) else np.nan, 'mean_bp': r.mean() * 1e4,
            'payoff': (w.mean() / -l.mean()) if len(w) and len(l) and l.mean() < 0 else np.nan,
            'pf': (w.sum() / -l.sum()) if len(l) and l.sum() < 0 else np.nan, 't_day': tc}


def fmt(s):
    if s['n'] < 5:
        return '%-28s n=%d' % (s['label'], s['n'])
    return '%-28s n=%6d win=%5.1f%% mean=%+7.2fbp payoff=%.2f pf=%.2f t_day=%+.2f' % (
        s['label'], s['n'], 100 * s['win'], s['mean_bp'], s['payoff'], s['pf'], s['t_day'])
