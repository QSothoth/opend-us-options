import numpy as np, pickle, os, lab, feat, idx, sig
from collections import defaultdict
from ev import ctx, add_delayed, emit
from common import feats, add_market, VALID_START
F = feats()
if not os.path.exists('feats_new.pkl'):
    d = lab.load('new1m.csv.gz', 'days_new.pkl')
    Fn = {k: feat.build(t, a) for k, (t, a) in d.items()}
    add_market(Fn); pickle.dump(Fn, open('feats_new.pkl', 'wb'))
Fn = pickle.load(open('feats_new.pkl', 'rb'))
for k, f in Fn.items(): f['_new'] = True
ALL = dict(F); ALL.update({k: v for k, v in Fn.items()})
add_delayed(ALL); idx.attach(ALL, 'HK.800000', 'hsi')
def seg(k):
    if ALL[k].get('_new'): return 'new'
    return 'valid' if k[1] >= VALID_START else 'train'
CANDS = {'C1': dict(thr5=5, rsi=65), 'C2': dict(thr5=6, rsi=60), 'C3': dict(thr5=8, rsi=65),
         'C4': dict(thr5=5, rsi=65, thr15=12), 'C5': dict(thr5=4, rsi=60)}
H = ('d1f15', 'd1f30', 'd1fclose')
WATCH = {'HK.02513', 'HK.09988', 'HK.01810', 'HK.00100'}
def collect(rule):
    """per signal: (seg, day, code, side, {h: ret}); plus stock-day counts per seg."""
    out = []; nsd = defaultdict(int)
    for k, f in ALL.items():
        s = seg(k); nsd[s] += 1
        if rule == 'current':
            ev = [(i, side) for i, side, tier in feat.watcher_marks(f)]
        else:
            L, S = sig.fade(f, ctx(f), **rule) if isinstance(rule, dict) else rule(f)
            ev = emit(L, S, len(f['c']))
        for i, side in ev:
            out.append((s, k[1], k[0], side, {h: side * f[h][i] for h in H}))
    return out, nsd
def stats(rows, nsd_n, h='d1f30'):
    s = lab.summarize([r[4][h] for r in rows], [r[1] for r in rows])
    s['dens'] = len(rows) / max(nsd_n, 1); return s
def line(name, s):
    if s['n'] < 5: return '%-30s n=%d' % (name, s['n'])
    return '%-30s %5.2f/sd n=%6d mean=%+6.2fbp hit=%5.1f%% payoff=%.2f pf=%.2f t_day=%+5.1f' % (
        name, s.get('dens', 0), s['n'], s['mean_bp'], 100 * s['hit'], s['payoff'], s['pf'], s['t_day'])
R = {c: collect(r) for c, r in list(CANDS.items()) + [('current', 'current')]}
def eligible(s): return s['dens'] >= 1.0 and s['mean_bp'] >= 4 and s['hit'] >= 0.55
print('== TRAIN (selection) d1f30')
tr = {}
for c, (rows, nsd) in R.items():
    tr[c] = stats([r for r in rows if r[0] == 'train'], nsd['train']); print(line(c, tr[c]))
elig = [c for c in CANDS if eligible(tr[c])]
pick = max(elig, key=lambda c: tr[c]['t_day']); print('eligible', elig, '-> PICK', pick)
# LODO
train_days = sorted({r[1] for r in R['C1'][0] if r[0] == 'train'})
nsd_day = defaultdict(int)
for k in ALL:
    if seg(k) == 'train': nsd_day[k[1]] += 1
oos = []; picks = defaultdict(int)
per_day = {c: defaultdict(list) for c in CANDS}
for c in CANDS:
    for r in R[c][0]:
        if r[0] == 'train': per_day[c][r[1]].append(r)
for d in train_days:
    st = {}
    for c in CANDS:
        rows = [r for dd, v in per_day[c].items() if dd != d for r in v]
        st[c] = stats(rows, sum(n for dd, n in nsd_day.items() if dd != d))
    el = [c for c in CANDS if eligible(st[c])]
    p = max(el, key=lambda c: st[c]['t_day']); picks[p] += 1
    oos += per_day[p][d]
print('LODO picks', dict(picks)); print(line('LODO out-of-sample', stats(oos, sum(nsd_day.values()))))
for segname in ('valid', 'new'):
    print('\n== %s' % segname.upper())
    for c in ['current', pick] + [x for x in CANDS if x != pick]:
        rows, nsd = R[c]; rs = [r for r in rows if r[0] == segname]
        for h in H:
            print(line('%s %s%s' % (c, h, ' <PICK' if c == pick and h == 'd1f30' else ''), stats(rs, nsd[segname], h)))
    rows, nsd = R[pick]; rs = [r for r in rows if r[0] == segname]
    for side, nm in ((1, 'BUY'), (-1, 'SELL')):
        print(line('%s %s d1f30' % (pick, nm), stats([r for r in rs if r[3] == side], nsd[segname])))
    for code in sorted(WATCH):
        n = sum(1 for k in ALL if k[0] == code and seg(k) == segname)
        print(line('%s %s d1f30' % (pick, code), stats([r for r in rs if r[2] == code], n)))
strong = collect(dict(thr5=8, rsi=70))
for segname in ('train', 'valid', 'new'):
    print(line('strong k8 r70 %s' % segname, stats([r for r in strong[0] if r[0] == segname], strong[1][segname])))
