#!/usr/bin/env python3
"""R1 backtest: does cross-sectional selection rescue the opening-window trade?

Offline, standard library only.  Reads the 1m csv.gz produced by `fetch_1m.py`
and the tick table from `ticks.csv`.  Every rule is the one written down in
notes/R1_PREREG.md before any result was seen.

    python3 studies/hk_open_scan/code/backtest.py --bars hk1m.csv.gz --ticks ticks.csv
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import random
import statistics as st
from collections import defaultdict

AUCTION = '09:30'          # the HK opening auction print, not a traded minute
ENTRY = '09:45'            # end of the user's second window
EXIT = '15:59'             # last continuous minute; 16:00 is the closing auction
BOS_WINDOW = '09:35'       # "first five minutes" for the structure break
MIN_BASE_DAYS = 10         # a rolling median needs this many prior days
BASE_DAYS = 20
MAX_TICK_BP = 15.0         # ex-ante cost filter
MIN_TURNOVER = 5e7         # HK$50m median daily turnover, prior 20 days
TICKS_PER_ROUND_TRIP = 2


def load_bars(path):
    """{(code, date): {hh:mm: (o,h,l,c,v,turnover,last_close)}}"""
    day = defaultdict(dict)
    op = gzip.open if path.endswith('.gz') else open
    with op(path, 'rt', encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            t = row['time_key']
            day[(row['code'], t[:10])][t[11:16]] = (
                float(row['open']), float(row['high']), float(row['low']),
                float(row['close']), float(row['volume']), float(row['turnover']),
                float(row['last_close']))
    return day


def session(bars):
    """One stock-day reduced to the features the prereg allows at 09:45."""
    auc = bars.get(AUCTION)
    entry_bar = bars.get(ENTRY)
    exit_bar = bars.get(EXIT)
    if not (auc and entry_bar and exit_bar):
        return None
    prev_close = auc[6]
    if prev_close <= 0 or auc[3] <= 0:
        return None
    win = [(t, b) for t, b in bars.items() if '09:31' <= t <= ENTRY]
    if len(win) < 10:                      # a barely-traded name, not a candidate
        return None
    win.sort()
    vol = sum(b[4] for _, b in win)
    turn = sum(b[5] for _, b in win)
    highs = [b[1] for _, b in win]
    lows = [b[2] for _, b in win]
    hi = max(highs + [auc[3]])
    lo = min(lows + [auc[3]])
    first5 = [b[1] for t, b in win if t <= BOS_WINDOW]
    entry = entry_bar[3]
    return {
        'prev_close': prev_close,
        'auc_px': auc[3],
        'auc_vol': auc[4],
        'auc_turnover': auc[5],
        'gap': auc[3] / prev_close - 1.0,
        'open_vol': auc[4] + vol,
        'vwap': (turn + auc[5]) / (vol + auc[4]) if (vol + auc[4]) > 0 else entry,
        'pos': (entry - lo) / (hi - lo) if hi > lo else 0.5,
        'bos5': bool(first5) and entry > max(first5),
        'entry': entry,
        'entry_auc': win[0][1][0],   # 09:31 open: first tradable price after the auction
        'exit': exit_bar[3],
        'exit_cas': bars.get('16:00', exit_bar)[3],
        'exit_30': bars.get('10:15', exit_bar)[3],
        'exit_60': bars.get('10:45', exit_bar)[3],
        'day_turnover': sum(b[5] for b in bars.values()),
    }


def rolling_median(history, n=BASE_DAYS):
    tail = history[-n:]
    return st.median(tail) if len(tail) >= MIN_BASE_DAYS else None


def build(day_bars, ticks):
    """Per (date, code) rows carrying only strictly-prior baselines."""
    per_code = defaultdict(list)
    for (code, date), bars in day_bars.items():
        s = session(bars)
        if s:
            per_code[code].append((date, s))
    rows = defaultdict(dict)
    for code, days in per_code.items():
        days.sort()
        hist_auc, hist_open, hist_turn, hist_auc_turn = [], [], [], []
        for date, s in days:
            base_auc = rolling_median(hist_auc)
            base_open = rolling_median(hist_open)
            base_turn = rolling_median(hist_turn)
            base_auc_turn = rolling_median(hist_auc_turn)
            hist_auc.append(s['auc_vol'])
            hist_open.append(s['open_vol'])
            hist_turn.append(s['day_turnover'])
            hist_auc_turn.append(s['auc_turnover'])
            if None in (base_auc, base_open, base_turn) or base_auc <= 0 or base_open <= 0:
                continue
            tick = ticks.get(code)
            if not tick:
                continue
            tick_bp = tick / s['entry'] * 1e4
            if tick_bp > MAX_TICK_BP or base_turn < MIN_TURNOVER:
                continue
            cost = TICKS_PER_ROUND_TRIP * tick / s['entry']
            rows[date][code] = dict(
                s,
                auc_rvol=s['auc_vol'] / base_auc,
                open_rvol=s['open_vol'] / base_open,
                tick_bp=tick_bp,
                gross=s['exit'] / s['entry'] - 1.0,
                gross_cas=s['exit_cas'] / s['entry'] - 1.0,
                net_30=s['exit_30'] / s['entry'] - 1.0 - cost,
                net_60=s['exit_60'] / s['entry'] - 1.0 - cost,
                net=s['exit'] / s['entry'] - 1.0 - cost,
                auc_turn_rvol=s['auc_turnover'] / base_auc_turn if base_auc_turn else 0.0,
                net_auc=s['exit'] / s['entry_auc'] - 1.0
                        - TICKS_PER_ROUND_TRIP * tick / s['entry_auc'],
            )
    return rows


CANDIDATES = {
    'C1_auction_rvol': lambda r: r['auc_rvol'],
    'C2_gap': lambda r: r['gap'],
    'C3_open_rvol': lambda r: r['open_rvol'],
}


R2_CANDIDATES = {
    'D1_auction_vol_rvol': lambda r: r['auc_rvol'],
    'D2_gap': lambda r: r['gap'],
    'D4_auction_turnover_rvol': lambda r: r['auc_turn_rvol'],
}


def d3(rows):
    return sorted([r for r in rows if r['gap'] > 0], key=lambda r: r['auc_rvol'], reverse=True)


def d5(rows):
    return sorted([r for r in rows if r['auc_turnover'] >= 5e6], key=lambda r: r['gap'], reverse=True)


def c4(rows):
    ok = [r for r in rows if r['gap'] > 0 and r['entry'] > r['vwap'] and r['pos'] >= 0.5]
    return sorted(ok, key=lambda r: r['open_rvol'], reverse=True)


def c5(rows):
    top = sorted(rows, key=lambda r: r['open_rvol'], reverse=True)[:30]
    return [r for r in top if r['bos5']]


def paired(diffs):
    """mean, t, and the share of days the basket beat the universe."""
    n = len(diffs)
    if n < 2:
        return 0.0, 0.0, 0.0
    m = sum(diffs) / n
    sd = st.stdev(diffs)
    t = m / (sd / n ** 0.5) if sd > 0 else 0.0
    return m, t, sum(1 for d in diffs if d > 0) / n


def run(rows, k, key='net', seed=7, arm='R1'):
    dates = sorted(rows)
    out = {}
    rnd = random.Random(seed)
    table = CANDIDATES if arm == 'R1' else R2_CANDIDATES
    extra = (['C4_rvol_and_form', 'C5_rvol_and_bos'] if arm == 'R1'
             else ['D3_gap_then_rvol', 'D5_gap_with_money'])
    for name in list(table) + extra + ['RANDOM']:
        diffs, picks, trades = [], [], []
        for date in dates:
            day = list(rows[date].values())
            if len(day) < k * 2:
                continue
            uni = sum(r[key] for r in day) / len(day)
            if name == 'C4_rvol_and_form':
                sel = c4(day)[:k]
            elif name == 'C5_rvol_and_bos':
                sel = c5(day)[:k]
            elif name == 'D3_gap_then_rvol':
                sel = d3(day)[:k]
            elif name == 'D5_gap_with_money':
                sel = d5(day)[:k]
            elif name == 'RANDOM':
                sel = [rnd.sample(day, k) for _ in range(200)]
                got = [sum(r[key] for r in s) / k for s in sel]
                diffs.append(sum(got) / len(got) - uni)
                picks.append(k)
                continue
            else:
                sel = sorted(day, key=table[name], reverse=True)[:k]
            if not sel:
                continue
            diffs.append(sum(r[key] for r in sel) / len(sel) - uni)
            picks.append(len(sel))
            trades += [r[key] for r in sel]
        m, t, hit = paired(diffs)
        # one lucky session can carry a 70-day mean; report the mean without it
        drop_best = sorted(diffs)[:-1]
        m_ex = sum(drop_best) / len(drop_best) if drop_best else 0.0
        wins = [x for x in trades if x > 0]
        losses = [-x for x in trades if x < 0]
        out[name] = {
            'days': len(diffs), 'avg_picks': round(sum(picks) / len(picks), 1) if picks else 0,
            'excess_per_day_bp': round(m * 1e4, 1), 't': round(t, 2),
            'excess_ex_best_day_bp': round(m_ex * 1e4, 1),
            'day_hit': round(hit, 3), 'n_trades': len(trades),
            'trade_mean_bp': round(sum(trades) / len(trades) * 1e4, 1) if trades else 0.0,
            'trade_hit': round(len(wins) / len(trades), 3) if trades else 0.0,
            'payoff': round((sum(wins) / len(wins)) / (sum(losses) / len(losses)), 2)
                      if wins and losses else 0.0,
        }
    return out


def permutation(rows, k, key, n=200, seed=11, arm='R1'):
    """Shuffle each day's returns across names.  A ranking with no look-ahead and
    no edge cannot beat its own shuffled null, and with a handful of days the
    null is wide -- which is the point of measuring it rather than trusting t."""
    real = {name: r['excess_per_day_bp'] for name, r in run(rows, k, key=key, arm=arm).items()}
    rnd = random.Random(seed)
    null = defaultdict(list)
    for _ in range(n):
        shuf = {}
        for date, day in rows.items():
            codes = list(day)
            vals = [day[c][key] for c in codes]
            rnd.shuffle(vals)
            shuf[date] = {c: dict(day[c], **{key: v}) for c, v in zip(codes, vals)}
        for name, r in run(shuf, k, key=key, arm=arm).items():
            null[name].append(r['excess_per_day_bp'])
    out = {}
    for name, got in real.items():
        draws = null[name]
        out[name] = {
            'real_bp': got,
            'null_p95_bp': round(sorted(draws)[int(0.95 * len(draws))], 1),
            'p_value': round(sum(1 for d in draws if d >= got) / len(draws), 3),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bars', required=True)
    ap.add_argument('--ticks', required=True)
    ap.add_argument('--out')
    ap.add_argument('--perm', type=int, default=0,
                    help='shuffled-null replications for the pre-registered metric')
    a = ap.parse_args()
    ticks = {r['code']: float(r['price_spread']) for r in csv.DictReader(open(a.ticks))
             if r['price_spread'] not in ('', 'nan')}
    day_bars = load_bars(a.bars)
    rows = build(day_bars, ticks)
    dates = sorted(rows)
    print('stock-days %d  trading days %d  %s -> %s  names/day %.0f' % (
        sum(len(v) for v in rows.values()), len(dates), dates[0], dates[-1],
        sum(len(v) for v in rows.values()) / len(dates)))
    result = {}
    print('\n########## R2: decide on the auction alone, buy the 09:31 open ##########')
    for k in (5, 10):
        res = run(rows, k, key='net_auc', arm='R2')
        result['R2_net_auc_K%d' % k] = res
        print('\n=== K=%d  09:31 -> 15:59  net of 2 ticks ===' % k)
        print('%-26s %5s %6s %8s %9s %6s %7s %8s %7s %7s' % (
            'candidate', 'days', 'picks', 'exc/day', 'ex-best', 't', 'dayhit',
            'trades', 'mean', 'payoff'))
        for name, r in res.items():
            print('%-26s %5d %6.1f %8.1f %9.1f %6.2f %7.3f %8d %7.1f %7.2f' % (
                name, r['days'], r['avg_picks'], r['excess_per_day_bp'],
                r['excess_ex_best_day_bp'], r['t'], r['day_hit'], r['n_trades'],
                r['trade_mean_bp'], r['payoff']))
    print('\n########## R1: decide at 09:45, buy the 09:45 close ##########')
    # 09:45 -> 15:59 net is the pre-registered decision metric; the shorter
    # horizons below it are descriptive only and choose nothing.
    for key, label in (('net', 'net of 2 ticks'), ('gross', 'gross'),
                       ('net_30', 'net, exit 10:15 (descriptive)'),
                       ('net_60', 'net, exit 10:45 (descriptive)')):
        for k in (5, 10):
            res = run(rows, k, key=key)
            result['%s_K%d' % (key, k)] = res
            print('\n=== K=%d  09:45 -> 15:59  %s ===' % (k, label))
            print('%-20s %5s %6s %8s %9s %6s %7s %8s %7s %7s' % (
                'candidate', 'days', 'picks', 'exc/day', 'ex-best', 't', 'dayhit',
                'trades', 'mean', 'payoff'))
            for name, r in res.items():
                print('%-20s %5d %6.1f %8.1f %9.1f %6.2f %7.3f %8d %7.1f %7.2f' % (
                    name, r['days'], r['avg_picks'], r['excess_per_day_bp'],
                    r['excess_ex_best_day_bp'], r['t'], r['day_hit'], r['n_trades'],
                    r['trade_mean_bp'], r['payoff']))
    if a.perm:
        for arm, key, label in (('R2', 'net_auc', '09:31 -> 15:59'),
                                ('R1', 'net', '09:45 -> 15:59')):
            for k in (5, 10):
                res = permutation(rows, k, key, n=a.perm, arm=arm)
                result['perm_%s_K%d' % (arm, k)] = res
                print('\n=== %s permutation null, K=%d, %s, %d shuffles ==='
                      % (arm, k, label, a.perm))
                print('%-26s %10s %12s %8s' % ('candidate', 'real bp', 'null p95', 'p'))
                for name, r in res.items():
                    print('%-26s %10.1f %12.1f %8.3f'
                          % (name, r['real_bp'], r['null_p95_bp'], r['p_value']))
    if a.out:
        json.dump(result, open(a.out, 'w'), indent=1)


if __name__ == '__main__':
    main()
