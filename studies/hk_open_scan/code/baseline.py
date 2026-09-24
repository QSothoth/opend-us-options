#!/usr/bin/env python3
"""Build the rolling per-stock baselines the scanner needs, at zero quota cost.

`get_cur_kline(code, 1000, K_1M)` returns roughly three HK sessions and costs a
subscription slot, not history quota.  Two measured OpenD behaviours shape this
script:

* A slot is **not** returned by `unsubscribe`, nor by closing the context --
  after both, `query_subscription` still reported 5/295.  Release is on OpenD's
  own timer.  With a cap of 300 the 662-name universe therefore cannot be
  walked in one run, so each run takes the 300 least-recently-seen names and
  the store fills in over a few sessions.
* `get_cur_kline` called immediately after `subscribe` returns a partial frame
  (measured: 60 rows instead of 1000).  Wait first.

Run it after the close, once per trading day.

    python3 code/baseline.py --out baseline.json --universe universe.json
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics as st
import time
from collections import defaultdict
from pathlib import Path

logging.getLogger('futu').setLevel(logging.ERROR)
logging.getLogger().setLevel(logging.ERROR)

PLATES = ('HK.GangGuTong', 'HK.LIST1922')
BLOCK = 100                # subscribe in blocks this size
MAX_NAMES = 300            # the whole subscription cap; see the note above
SUBSCRIBE_SETTLE = 3.0     # seconds before the first get_cur_kline of a block
KEEP_DAYS = 20


def universe(ctx, ft, path):
    if path and Path(path).exists():
        return sorted(json.loads(Path(path).read_text(encoding='utf-8')))
    codes = set()
    for plate in PLATES:
        ret, frame = ctx.get_plate_stock(plate)
        if ret != ft.RET_OK:
            raise RuntimeError('get_plate_stock %s: %s' % (plate, frame))
        codes |= {str(r['code']) for _, r in frame.iterrows()
                  if str(r.get('stock_type')) == 'STOCK'}
    return sorted(codes)


def fold(frame):
    """One code's 1m frame -> per-session turnover buckets and the prices the
    breadth check needs.  09:30 is the opening auction print, not a traded
    minute, so it gets its own bucket."""
    per_day = defaultdict(lambda: {'auction': 0.0, 'open15': 0.0, 'day': 0.0})
    for row in frame.itertuples(index=False):
        t = str(row.time_key)
        d, hm = t[:10], t[11:16]
        turn = float(row.turnover or 0)
        per_day[d]['day'] += turn
        if hm == '09:30':
            per_day[d]['auction'] += turn
            per_day[d]['auc_px'] = float(row.close)
            per_day[d]['prev_close'] = float(row.last_close)
        if '09:30' <= hm <= '09:45':
            per_day[d]['open15'] += turn
        if hm in ('09:31', '09:45', '15:59'):
            per_day[d]['px_' + hm.replace(':', '')] = float(row.close)
    return per_day


def seed_from_bars(path, store):
    """Seed the store from a 1m csv.gz that `fetch_1m.py` already produced, so a
    universe with real history does not have to wait ten sessions for OpenD to
    hand back subscription slots."""
    import csv
    import gzip
    opener = gzip.open if str(path).endswith('.gz') else open
    frames = defaultdict(list)
    with opener(path, 'rt', encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            frames[row['code']].append(row)
    for code, rows in frames.items():
        per_day = defaultdict(lambda: {'auction': 0.0, 'open15': 0.0, 'day': 0.0})
        for r in rows:
            d, hm = r['time_key'][:10], r['time_key'][11:16]
            turn = float(r['turnover'] or 0)
            per_day[d]['day'] += turn
            if hm == '09:30':
                per_day[d]['auction'] += turn
                per_day[d]['auc_px'] = float(r['close'])
                per_day[d]['prev_close'] = float(r['last_close'])
            if '09:30' <= hm <= '09:45':
                per_day[d]['open15'] += turn
            if hm in ('09:31', '09:45', '15:59'):
                per_day[d]['px_' + hm.replace(':', '')] = float(r['close'])
        rec = store.setdefault(code, {'days': {}})
        for d, v in per_day.items():
            if v['day'] > 0 and 'px_1559' in v:
                rec['days'][d] = v
        rec['days'] = dict(sorted(rec['days'].items())[-KEEP_DAYS:])
        days = list(rec['days'].values())
        if len(days) >= 5:
            rec['auction_turnover'] = st.median(x['auction'] for x in days)
            rec['open15_turnover'] = st.median(x['open15'] for x in days)
            rec['day_turnover'] = st.median(x['day'] for x in days)
            rec['n_days'] = len(days)
    return store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--from-bars', default=None,
                    help='seed from a fetch_1m.py csv.gz instead of calling OpenD')
    ap.add_argument('--universe', default=None)
    ap.add_argument('--max-names', type=int, default=MAX_NAMES)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=11111)
    a = ap.parse_args()

    out = Path(a.out)
    store = json.loads(out.read_text(encoding='utf-8')) if out.exists() else {}
    if a.from_bars:
        seed_from_bars(a.from_bars, store)
        out.write_text(json.dumps(store), encoding='utf-8')
        ready = sum(1 for v in store.values() if v.get('n_days', 0) >= 10)
        print('seeded %s from %s: %d names, %d with >=10 sessions'
              % (a.out, a.from_bars, len(store), ready))
        return

    import futu as ft
    ctx = ft.OpenQuoteContext(host=a.host, port=a.port)
    try:
        codes = universe(ctx, ft, a.universe)
        # least recently seen first, so consecutive runs cover the universe
        codes.sort(key=lambda c: store.get(c, {}).get('seen', ''))
        ret, sub = ctx.query_subscription()
        free = sub.get('remain', 0) if ret == ft.RET_OK else 0
        todo = codes[:min(a.max_names, free)]
        print('universe %d, slots free %d, this run %d' % (len(codes), free, len(todo)), flush=True)
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')

        for start in range(0, len(todo), BLOCK):
            block = todo[start:start + BLOCK]
            ret, err = ctx.subscribe(block, [ft.SubType.K_1M], subscribe_push=False)
            if ret != ft.RET_OK:
                print('subscribe failed at %d: %s' % (start, err), flush=True)
                break
            time.sleep(SUBSCRIBE_SETTLE)
            for code in block:
                ret, frame = ctx.get_cur_kline(code, 1000, ft.KLType.K_1M, ft.AuType.QFQ)
                if ret != ft.RET_OK or frame is None or not len(frame):
                    continue
                rec = store.setdefault(code, {'days': {}})
                rec['seen'] = stamp
                for d, v in fold(frame).items():
                    # a day still in progress, or a halted one, teaches nothing
                    if v['day'] > 0 and 'px_1559' in v:
                        rec['days'][d] = v
                rec['days'] = dict(sorted(rec['days'].items())[-KEEP_DAYS:])
                days = list(rec['days'].values())
                if len(days) >= 5:
                    rec['auction_turnover'] = st.median(x['auction'] for x in days)
                    rec['open15_turnover'] = st.median(x['open15'] for x in days)
                    rec['day_turnover'] = st.median(x['day'] for x in days)
                    rec['n_days'] = len(days)
            ctx.unsubscribe(block, [ft.SubType.K_1M])
            print('%d/%d' % (min(start + BLOCK, len(todo)), len(todo)), flush=True)

        out.write_text(json.dumps(store), encoding='utf-8')
        ready = sum(1 for v in store.values() if v.get('n_days', 0) >= 10)
        covered = sum(1 for v in store.values() if v.get('days'))
        print('wrote %s: %d names, %d with any history, %d with >=10 sessions'
              % (a.out, len(store), covered, ready))
        if ready < len(store):
            print('run again on the next trading days; slots release on OpenD\'s own timer')
    finally:
        ctx.close()


if __name__ == '__main__':
    main()
