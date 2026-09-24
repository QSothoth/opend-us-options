#!/usr/bin/env python3
"""Measure the three things the watcher's own README could not know.

1. The 09:30 bar is the opening auction, not a traded minute -- how big is it
   relative to a normal minute, and what does feeding it to a 20-bar volume mean
   do to `vol_ratio` during the first twenty minutes?
2. `vol_ratio` divides by a window that includes the bar being measured.
3. `marks()` recomputes every indicator from bar 0 for every bar, so one sweep
   costs O(n^2) -- fine for ten codes, decisive for six hundred.

    python3 code/review_watch.py --bars hk1m.csv.gz
"""
from __future__ import annotations

import argparse
import csv
import gzip
import importlib.util
import statistics as st
import sys
import time
import types
from collections import defaultdict
from pathlib import Path


def load_watcher():
    stub = types.ModuleType('futu')
    stub.AuType = stub.KLType = stub.SubType = type('X', (), {'K_1M': None, 'NONE': None})
    stub.OpenQuoteContext = object
    stub.RET_OK = 0
    sys.modules.setdefault('futu', stub)
    path = Path(__file__).resolve().parents[3] / 'watch' / 'smc_watch.py'
    spec = importlib.util.spec_from_file_location('watcher', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_days(path):
    days = defaultdict(list)
    op = gzip.open if path.endswith('.gz') else open
    with op(path, 'rt', encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            days[(r['code'], r['time_key'][:10])].append(
                (r['time_key'], float(r['open']), float(r['high']),
                 float(r['low']), float(r['close']), float(r['volume'])))
    for v in days.values():
        v.sort()
    return days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bars', required=True)
    a = ap.parse_args()
    w = load_watcher()
    days = load_days(a.bars)
    print('stock-days %d' % len(days))

    # 1. how large is the auction print next to a normal minute
    ratios, doji, zero = [], 0, 0
    contaminated, clean = [], []
    for bars in days.values():
        if len(bars) < 60 or bars[0][0][11:16] != '09:30':
            continue
        auc = bars[0]
        if auc[1] == auc[2] == auc[3] == auc[4]:
            doji += 1
        if auc[5] == 0:
            zero += 1
            continue
        med = st.median(b[5] for b in bars[1:31])
        if med > 0:
            ratios.append(auc[5] / med)
        # vol_ratio for bars 1..20 with and without the auction bar in the window
        for i in range(1, 21):
            win = bars[max(0, i - 19):i + 1]
            avg = sum(b[5] for b in win) / len(win)
            win_no = [b for b in win if b[0][11:16] != '09:30']
            avg_no = sum(b[5] for b in win_no) / len(win_no) if win_no else 0
            if avg > 0 and avg_no > 0:
                contaminated.append(bars[i][5] / avg)
                clean.append(bars[i][5] / avg_no)
    print('\n1. the 09:30 bar')
    print('   O=H=L=C on %d of %d stock-days (%.1f%%); zero-volume auctions %d'
          % (doji, len(days), 100 * doji / len(days), zero))
    print('   auction volume / median of the next 30 minutes: median %.1fx  p90 %.1fx  max %.0fx'
          % (st.median(ratios), sorted(ratios)[int(.9 * len(ratios))], max(ratios)))
    drop = [c / n for c, n in zip(contaminated, clean) if n > 0]
    print('   vol_ratio on bars 1..20 is %.1f%% of what it would be without the auction bar'
          ' (median over %d bars)' % (100 * st.median(drop), len(drop)))
    over = sum(1 for c, n in zip(contaminated, clean) if (n >= w.VOL_MULT) != (c >= w.VOL_MULT))
    print('   %d of %d of those bars (%.2f%%) change side of VOL_MULT=%.1f either way'
          % (over, len(clean), 100 * over / len(clean), w.VOL_MULT))
    big = sum(1 for r in ratios if r >= 3)
    print('   BUT the auction is >=3x a normal minute on %d of %d stock-days (%.1f%%):'
          ' those are exactly the days a chase screen wants, and there the bar is'
          ' not a minute of trade at all' % (big, len(ratios), 100 * big / len(ratios)))

    # 2. self-inclusion in the trailing mean
    self_inc = []
    for bars in days.values():
        for i in range(w.VOL_LOOKBACK, len(bars)):
            win = bars[i - w.VOL_LOOKBACK + 1:i + 1]
            avg = sum(b[5] for b in win) / len(win)
            prev = sum(b[5] for b in win[:-1]) / (len(win) - 1)
            if avg > 0 and prev > 0 and bars[i][5] > 0:
                self_inc.append((bars[i][5] / avg) / (bars[i][5] / prev))
    print('\n2. vol_ratio divides by a window containing the bar itself')
    print('   reported / trailing-only: median %.3f  (a 10x bar reads as %.1fx)'
          % (st.median(self_inc), 10.0 / ((19 * 1 + 10) / 20)))

    # 2b. what the 'vol' tier can and cannot contain
    tier_trig = defaultdict(int)
    for bars in list(days.values()):
        ms, _ = w.marks([(b[0], b[1], b[2], b[3], b[4], b[5]) for b in bars])
        for m in ms:
            tier_trig[(m['tier'], m['side'], 'BOS' in m['trigger'])] += 1
    print('\n2b. the volume tier requires pos<=0.5 for a BUY -- the bottom half of'
          ' the day so far')
    for side in ('BUY', 'SELL'):
        for tier in ('plain', 'vol'):
            bos = tier_trig[(tier, side, True)]
            nob = tier_trig[(tier, side, False)]
            tot = bos + nob
            print('   %-5s %-5s %6d marks, %5.1f%% of them a structure break'
                  % (side, tier, tot, 100 * bos / tot if tot else 0))

    # 3. cost of one sweep
    sample = [b for b in days.values() if len(b) > 300][:20]
    t0 = time.time()
    for bars in sample:
        w.marks([(b[0], b[1], b[2], b[3], b[4], b[5]) for b in bars])
    per = (time.time() - t0) / len(sample)
    print('\n3. marks() recomputes every indicator from bar 0 for every bar')
    print('   %.0f ms per full session per code -> %.0f s to sweep 662 names once'
          % (per * 1000, per * 662))
    print('   the watcher polls every %ds, so the universe does not fit in one poll'
          % w.POLL_SEC)
    print('\n   WARMUP_BARS=%d: the first mark can only land at bar %d = %s,'
          % (w.WARMUP_BARS, w.WARMUP_BARS, '09:%02d' % (30 + w.WARMUP_BARS)))
    print('   which is after the 09:31-09:45 window this study is about.')


if __name__ == '__main__':
    main()
