#!/usr/bin/env python3
"""Capture the whole HK Stock-Connect universe across the opening auction.

Read-only: `OpenQuoteContext` snapshots only.  Costs no history-kline quota and
no subscription quota, so it can cover all ~660 names at once (2 batches of 400,
about 1.5s per sweep).

Why it has to run live: OpenD keeps no history of the pre-open session.  The
first 1m bar of a HK day is 09:30, and that bar is the auction print itself
(O=H=L=C, volume = matched volume).  Everything between 09:00 and 09:22 -- the
reference price path, the order imbalance -- exists only while it is happening.

    python3 code/capture.py --out captures/            # today, 08:50 -> 09:50
    python3 code/capture.py --universe universe.json --out captures/
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logging.getLogger('futu').setLevel(logging.ERROR)
logging.getLogger().setLevel(logging.ERROR)

HKT = timezone(timedelta(hours=8))
PLATES = ('HK.GangGuTong', 'HK.LIST1922')   # 港股通(沪) / 港股通(深)
BATCH = 400                                  # snapshot codes per request
KEEP = ('code', 'update_time', 'last_price', 'open_price', 'high_price', 'low_price',
        'prev_close_price', 'volume', 'turnover', 'bid_price', 'ask_price', 'bid_vol',
        'ask_vol', 'bid_ask_ratio', 'volume_ratio', 'turnover_rate', 'amplitude',
        'avg_price', 'circular_market_val', 'sec_status')
TEXT = {'code', 'update_time', 'sec_status'}


def cadence(t):
    """Seconds to the next sweep, by how much the moment can still change."""
    hm = t.hour * 60 + t.minute
    if 9 * 60 + 13 <= hm <= 9 * 60 + 23:      # no-cancel period + random matching
        return 5
    if 9 * 60 + 30 <= hm <= 9 * 60 + 46:      # the first fifteen traded minutes
        return 10
    return 60


def universe(ctx, ft, path=None):
    if path and Path(path).exists():
        return sorted(json.loads(Path(path).read_text(encoding='utf-8')))
    out = {}
    for plate in PLATES:
        ret, frame = ctx.get_plate_stock(plate)
        if ret != ft.RET_OK:
            raise RuntimeError('get_plate_stock %s: %s' % (plate, frame))
        for _, row in frame.iterrows():
            if str(row.get('stock_type')) == 'STOCK':
                out[str(row['code'])] = str(row['stock_name'])
    if path:
        Path(path).write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
    return sorted(out)


def sweep(ctx, ft, batches):
    rows = []
    for codes in batches:
        try:
            ret, frame = ctx.get_market_snapshot(codes)
        except Exception as exc:                      # a dropped sweep is not fatal
            print('EXC %s' % exc, flush=True)
            continue
        if ret != ft.RET_OK:
            print('ERR %s' % str(frame)[:120], flush=True)
            continue
        for _, x in frame.iterrows():
            rows.append([None if (isinstance(x[k], float) and x[k] != x[k])
                         else (str(x[k]) if k in TEXT else float(x[k])) for k in KEEP])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='captures')
    ap.add_argument('--universe', default=None, help='cached {code: name} json')
    ap.add_argument('--until', default='09:50', help='HKT stop time')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=11111)
    a = ap.parse_args()

    import futu as ft
    ctx = ft.OpenQuoteContext(host=a.host, port=a.port)
    if 'Trade' in type(ctx).__name__:
        raise RuntimeError('read-only quote session: trade context rejected')
    try:
        codes = universe(ctx, ft, a.universe)
        batches = [codes[i:i + BATCH] for i in range(0, len(codes), BATCH)]
        hh, mm = (int(x) for x in a.until.split(':'))
        stop = datetime.now(HKT).replace(hour=hh, minute=mm, second=0, microsecond=0)
        Path(a.out).mkdir(parents=True, exist_ok=True)
        day = datetime.now(HKT).strftime('%Y-%m-%d')
        path = Path(a.out) / ('auction_%s.jsonl' % day)
        print('%d codes, %d batches, until %s -> %s' % (len(codes), len(batches), stop, path),
              flush=True)
        n = 0
        with open(path, 'a', encoding='utf-8') as fh:
            while True:
                now = datetime.now(HKT)
                rows = sweep(ctx, ft, batches)
                fh.write(json.dumps({'ts': now.isoformat(), 'fields': KEEP, 'rows': rows}) + '\n')
                fh.flush()
                n += 1
                if n % 10 == 1:
                    print('%s sweeps=%d rows=%d' % (now.strftime('%H:%M:%S'), n, len(rows)),
                          flush=True)
                if now >= stop:
                    break
                time.sleep(cadence(now))
        print('done sweeps=%d' % n, flush=True)
    finally:
        ctx.close()


if __name__ == '__main__':
    main()
