"""Side-balanced statistics with day-clustered t, shared by W6-W8.

bal = 0.5 * (mean over BUY + mean over SELL); it is a sum over days of
c_d = 0.5 * (S_buy,d / N_buy + S_sell,d / N_sell), so the t uses those c_d.
"""
import numpy as np
from collections import defaultdict


def contrib(rows, m):
    per = defaultdict(lambda: {1: 0.0, -1: 0.0}); N = {1: 0, -1: 0}
    for r in rows:
        x = r[m]
        if x == x:
            per[r['day']][r['side']] += x; N[r['side']] += 1
    if min(N.values()) == 0:
        return {}, N
    return {d: 0.5 * (v[1] / N[1] + v[-1] / N[-1]) for d, v in per.items()}, N


def bal(rows, m):
    c, N = contrib(rows, m)
    if len(c) < 3:
        return np.nan, np.nan, sum(N.values())
    a = np.array(list(c.values()))
    return a.sum() * 1e4, a.mean() / a.std(ddof=1) * np.sqrt(len(a)), sum(N.values())


def diff(yes, no, m):
    cy, _ = contrib(yes, m); cn, _ = contrib(no, m)
    days = sorted(set(cy) | set(cn))
    if len(days) < 3:
        return np.nan, np.nan
    a = np.array([cy.get(d, 0) - cn.get(d, 0) for d in days])
    return a.sum() * 1e4, a.mean() / a.std(ddof=1) * np.sqrt(len(a))


def side_mean(rows, s, m):
    x = [r[m] for r in rows if r['side'] == s and r[m] == r[m]]
    return (np.mean(x) * 1e4 if x else np.nan), len(x)
