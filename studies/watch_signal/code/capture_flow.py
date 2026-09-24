#!/usr/bin/env python3
"""Daily after-close capture of minute capital flow + 1m bars. Read-only, zero quota.

OpenD's intraday capital flow (net buy-initiated turnover by order size) only
exists for the latest session, so a day not captured is lost. 1m bars are
pulled only for codes already charged in the 30-day history quota.

    /opt/futu-opend/venv/bin/python capture_flow.py --out ../data/flow
"""
import argparse, csv, gzip, logging, os, time
from datetime import datetime, timedelta, timezone
logging.disable(logging.CRITICAL)
import futu as ft
logging.getLogger('FTConsoleLog').setLevel(logging.ERROR)

HKT = timezone(timedelta(hours=8))
FLOW_COLS = ('in_flow', 'super_in_flow', 'big_in_flow', 'mid_in_flow', 'sml_in_flow')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--extra', default='', help='comma list of extra codes (flow only unless charged)')
    a = ap.parse_args()
    day = datetime.now(HKT).strftime('%Y-%m-%d')
    os.makedirs(a.out, exist_ok=True)
    ctx = ft.OpenQuoteContext(host='127.0.0.1', port=11111)
    try:
        uni = set()
        for plate in ('HK.GangGuTong', 'HK.LIST1922'):
            ret, fr = ctx.get_plate_stock(plate)
            if ret == ft.RET_OK:
                uni |= {str(c) for c, t in zip(fr['code'], fr['stock_type']) if str(t) == 'STOCK'}
        ret, q = ctx.get_history_kl_quota(get_detail=True)
        charged = {x['code'] for x in q[2]} if ret == ft.RET_OK else set()
        codes = sorted((uni & charged) | {c for c in a.extra.split(',') if c})
        n_flow = n_bar = 0
        fp = os.path.join(a.out, 'flow_%s.csv.gz' % day)
        with gzip.open(fp + '.tmp', 'wt', newline='') as fh:
            w = csv.writer(fh); w.writerow(('code', 'time') + FLOW_COLS)
            for i, code in enumerate(codes):
                if i and i % 25 == 0:
                    time.sleep(31)             # the endpoint allows ~30 calls per 30s
                for _attempt in range(3):
                    ret, fr = ctx.get_capital_flow(code, period_type=ft.PeriodType.INTRADAY)
                    if ret == ft.RET_OK:
                        break
                    time.sleep(31)
                if ret != ft.RET_OK or len(fr) == 0 or not str(fr['capital_flow_item_time'].iloc[-1]).startswith(day):
                    print('skip %s: %s' % (code, str(fr)[:80] if ret != ft.RET_OK else 'stale'), flush=True)
                    continue
                for r in fr.itertuples(index=False):
                    w.writerow([code, r.capital_flow_item_time] + [getattr(r, c) for c in FLOW_COLS]); n_flow += 1
        os.replace(fp + '.tmp', fp)
        bp = os.path.join(a.out, 'bars_%s.csv.gz' % day)
        with gzip.open(bp + '.tmp', 'wt', newline='') as fh:
            w = csv.writer(fh); w.writerow(('code', 'time_key', 'open', 'high', 'low', 'close', 'volume', 'turnover'))
            for i, code in enumerate(sorted(set(codes) & charged | {'HK.800000'} & charged)):
                if i and i % 50 == 0:
                    time.sleep(30)
                ret, fr, _ = ctx.request_history_kline(code, start=day, end=day, ktype=ft.KLType.K_1M,
                                                       autype=ft.AuType.QFQ, max_count=1000)
                if ret != ft.RET_OK:
                    continue
                for r in fr.itertuples(index=False):
                    w.writerow([code, r.time_key, r.open, r.high, r.low, r.close, r.volume, r.turnover]); n_bar += 1
        os.replace(bp + '.tmp', bp)
        after = ctx.get_history_kl_quota(get_detail=False)[1][:2]
        print('%s flow rows %d bars rows %d codes %d quota %s' % (day, n_flow, n_bar, len(codes), after), flush=True)
    finally:
        ctx.close()


if __name__ == '__main__':
    main()
