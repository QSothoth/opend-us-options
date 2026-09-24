"""W2 evaluation, streamed per stock-day so the 17k-day out-of-sample set fits in memory."""
import numpy as np, lab, feat, sys
from collections import defaultdict
from gran import ticks_per_bar
GATE, FINE = 2.0, 5.0
WATCH = ('HK.02513', 'HK.00100', 'HK.09988', 'HK.01810')
def trade(f, i, side, stop, hold=30):
    """Enter next close; exit at the invalidation level if touched, else after `hold` bars."""
    c, h, l, o = f['c'], f['h'], f['l'], f['o']; n = len(c); e = i + 1
    if e >= n - 1: return np.nan
    px = c[e]
    if stop is None or np.isnan(stop) or (side > 0 and stop >= px) or (side < 0 and stop <= px): stop = None
    for j in range(e + 1, min(e + hold, n - 1) + 1):
        if stop is not None:
            if side > 0 and l[j] <= stop: return (min(o[j], stop) / px - 1)
            if side < 0 and h[j] >= stop: return -(max(o[j], stop) / px - 1)
    j = min(e + hold, n - 1)
    return side * (c[j] / px - 1)
def main(days, label):
    R = defaultdict(lambda: ([], [])); cnt = defaultdict(int); nsd = 0
    for (code, day), (t, a) in days.items():
        f = feat.build(t, a); nsd += 1
        for h in ('d1f30', 'd1fclose'):
            pass
        c = f['c']; n = len(c)
        d1f30 = np.full(n, np.nan); d1f30[:n - 31] = c[31:] / c[1:n - 30] - 1
        d1fc = np.full(n, np.nan); d1fc[:n - 1] = c[-1] / c[1:] - 1
        tpb = ticks_per_bar(f)
        for i, s, tier in feat.watcher_marks(f):
            g = 'kept' if tpb[i] >= GATE else 'gated'
            keys = [g, 'all', g + ('_buy' if s > 0 else '_sell')]
            if tpb[i] >= FINE: keys.append('fine')
            if code in WATCH: keys += [code + '_' + g]
            stop = f['sl'][i] if s > 0 else f['sh'][i]
            tr = trade(f, i, s, stop)
            for k in keys:
                cnt[k] += 1
                R[(k, 'd1f30')][0].append(s * d1f30[i]); R[(k, 'd1f30')][1].append(day)
                R[(k, 'd1fclose')][0].append(s * d1fc[i]); R[(k, 'd1fclose')][1].append(day)
                R[(k, 'trade')][0].append(tr); R[(k, 'trade')][1].append(day)
    print('== %s  stock-days %d' % (label, nsd))
    order = ['all', 'gated', 'kept', 'fine', 'gated_buy', 'gated_sell', 'kept_buy', 'kept_sell'] + \
            [c + '_' + g for c in WATCH for g in ('kept', 'gated')]
    for k in order:
        if cnt[k] == 0: continue
        parts = []
        for m in ('d1f30', 'd1fclose', 'trade'):
            s = lab.summarize(*R[(k, m)])
            if s['n'] < 5: parts.append('%s n<5' % m); continue
            parts.append('%s %+6.2fbp hit%4.1f%% po%.2f t%+5.1f' % (m, s['mean_bp'], 100 * s['hit'], s['payoff'], s['t_day']))
        print('%-16s %6.2f/sd n=%6d | ' % (k, cnt[k] / nsd, cnt[k]) + ' | '.join(parts), flush=True)
if __name__ == '__main__':
    path, cache, lo, hi, label = sys.argv[1:6]
    d = lab.load(path, cache)
    d = {k: v for k, v in d.items() if lo <= k[1] <= hi and not k[0].startswith('HK.8')}
    main(d, label)
