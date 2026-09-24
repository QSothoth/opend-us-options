#!/usr/bin/env python3
"""Pull 1m history for the backtest universe, without spending history quota.

OpenD charges the 300-per-30-days history-kline quota once per security; a
security already charged is free to re-read until it ages out.  The US 0DTE
mainline needs that quota every trading day for `custody freeze`, and expired
weeklies cannot be re-fetched, so this script defaults to the intersection of
the Stock-Connect universe with the already-charged list and refuses to charge
anything new unless `--allow-new` is passed.

    python3 code/fetch_1m.py --out hk1m.csv.gz --start 2026-08-24 --end 2026-09-21
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import logging
import time
from pathlib import Path

logging.getLogger('futu').setLevel(logging.ERROR)
logging.getLogger().setLevel(logging.ERROR)

PLATES = ('HK.GangGuTong', 'HK.LIST1922')
COLS = ('code', 'time_key', 'open', 'high', 'low', 'close', 'volume', 'turnover', 'last_close')
CALLS_PER_WINDOW = 50      # the history endpoint allows 60 per 30s; leave headroom
WINDOW_SEC = 30


class Throttle:
    def __init__(self):
        self.n, self.t0 = 0, time.time()

    def tick(self):
        self.n += 1
        if self.n >= CALLS_PER_WINDOW:
            wait = WINDOW_SEC - (time.time() - self.t0)
            if wait > 0:
                time.sleep(wait)
            self.n, self.t0 = 0, time.time()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--allow-new', action='store_true',
                    help='also charge quota for names not already charged')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=11111)
    a = ap.parse_args()

    import futu as ft
    ctx = ft.OpenQuoteContext(host=a.host, port=a.port)
    try:
        uni = set()
        for plate in PLATES:
            ret, frame = ctx.get_plate_stock(plate)
            if ret != ft.RET_OK:
                raise RuntimeError('get_plate_stock %s: %s' % (plate, frame))
            uni |= {str(r['code']) for _, r in frame.iterrows()
                    if str(r.get('stock_type')) == 'STOCK'}
        ret, quota = ctx.get_history_kl_quota(get_detail=True)
        charged = {x['code'] for x in quota[2]}
        codes = sorted(uni if a.allow_new else uni & charged)
        print('universe %d, already charged %d, fetching %d, quota remaining %d'
              % (len(uni), len(uni & charged), len(codes), quota[1]), flush=True)

        throttle = Throttle()
        n_rows = 0
        with gzip.open(a.out, 'wt', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(COLS)
            for i, code in enumerate(codes):
                key = None
                while True:
                    throttle.tick()
                    ret, frame, key = ctx.request_history_kline(
                        code, start=a.start, end=a.end, ktype=ft.KLType.K_1M,
                        autype=ft.AuType.QFQ, max_count=1000, page_req_key=key)
                    if ret != ft.RET_OK:
                        print('ERR %s %s' % (code, str(frame)[:100]), flush=True)
                        key = None
                        break
                    for row in frame.itertuples(index=False):
                        w.writerow([code, str(row.time_key), row.open, row.high, row.low,
                                    row.close, row.volume, row.turnover, row.last_close])
                        n_rows += 1
                    if key is None:
                        break
                if i % 20 == 0:
                    print('%d/%d %s rows=%d' % (i, len(codes), code, n_rows), flush=True)
        print('rows %d -> %s  quota remaining %d'
              % (n_rows, a.out, ctx.get_history_kl_quota(get_detail=False)[1][1]), flush=True)
    finally:
        ctx.close()


if __name__ == '__main__':
    main()
