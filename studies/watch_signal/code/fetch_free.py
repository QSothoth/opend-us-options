"""Fetch 1m history ONLY for codes already charged in the 30-day quota."""
import csv, gzip, sys, time, logging
logging.disable(logging.CRITICAL)
import futu as ft
out, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
codes = sys.argv[4].split(',')
ctx = ft.OpenQuoteContext(host='127.0.0.1', port=11111)
try:
    ret, q = ctx.get_history_kl_quota(get_detail=True)
    charged = {x['code'] for x in q[2]}
    miss = [c for c in codes if c not in charged]
    if miss: raise SystemExit('refuse, not charged: %s' % miss)
    n = 0; calls = 0; t0 = time.time()
    with gzip.open(out, 'wt', newline='') as fh:
        w = csv.writer(fh); w.writerow(('code', 'time_key', 'open', 'high', 'low', 'close', 'volume', 'turnover', 'last_close'))
        for i, code in enumerate(codes):
            key = None
            while True:
                calls += 1
                if calls % 50 == 0:
                    wait = 30 - (time.time() - t0)
                    if wait > 0: time.sleep(wait)
                    t0 = time.time()
                ret, fr, key = ctx.request_history_kline(code, start=start, end=end, ktype=ft.KLType.K_1M,
                                                         autype=ft.AuType.QFQ, max_count=1000, page_req_key=key)
                if ret != ft.RET_OK: print('ERR', code, str(fr)[:100], flush=True); break
                for r in fr.itertuples(index=False):
                    w.writerow([code, str(r.time_key), r.open, r.high, r.low, r.close, r.volume, r.turnover, r.last_close]); n += 1
                if key is None: break
            if i % 20 == 0: print(i, code, n, flush=True)
    print('rows', n, 'quota after', ctx.get_history_kl_quota(get_detail=False)[1][:2], flush=True)
finally:
    ctx.close()
