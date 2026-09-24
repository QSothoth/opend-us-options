#!/usr/bin/env python3
"""Rank the HK Stock-Connect universe in the two windows the study is about.

Read-only.  The universe pass is snapshot-driven on purpose: snapshots cost no
history-kline quota and no subscription quota, so all ~660 names fit in one
1.5s sweep, while `get_cur_kline` would need a subscription per code against a
cap of 300.

Two windows:

    --window auction   09:14-09:20 HKT, before the auction is matched
    --window open15    09:45 HKT, the end of the first fifteen traded minutes

Both print a ranking.  Neither prints a recommendation: see reports/ for what
the backtest does and does not support.  A factor whose rolling baseline is
missing is dropped from that name's score and the name is flagged, rather than
scored as if the factor were neutral.

    python3 code/scan.py --window open15 --baseline baseline.json --top 20
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logging.getLogger('futu').setLevel(logging.ERROR)
logging.getLogger().setLevel(logging.ERROR)

HKT = timezone(timedelta(hours=8))
PLATES = ('HK.GangGuTong', 'HK.LIST1922')
BATCH = 400
MAX_TICK_BP = 15.0        # same ex-ante cost filter the backtest uses
MIN_TURNOVER = 5e7        # HK$50m median daily turnover


def sweep(ctx, ft, codes):
    rows = {}
    for i in range(0, len(codes), BATCH):
        ret, frame = ctx.get_market_snapshot(codes[i:i + BATCH])
        if ret != ft.RET_OK:
            raise RuntimeError('get_market_snapshot: %s' % frame)
        for _, x in frame.iterrows():
            rows[str(x['code'])] = x
    return rows


def num(x, key):
    try:
        v = float(x[key])
        return None if v != v else v
    except Exception:
        return None


def score_rows(snap, baseline, window):
    """Factors, each either measured or explicitly absent."""
    out = []
    for code, x in snap.items():
        prev = num(x, 'prev_close_price')
        last = num(x, 'last_price')
        tick = num(x, 'price_spread')
        turn = num(x, 'turnover') or 0.0
        vol = num(x, 'volume') or 0.0
        if not (prev and last and tick) or str(x.get('sec_status')) != 'NORMAL':
            continue
        tick_bp = tick / last * 1e4
        base = baseline.get(code, {})
        missing = []
        row = {
            'code': code, 'name': str(x['name']), 'last': last,
            'gap': last / prev - 1.0,
            'tick_bp': tick_bp,
            'turnover': turn,
            'vwap': num(x, 'avg_price'),
            'vs_vwap': (last / num(x, 'avg_price') - 1.0) if num(x, 'avg_price') else None,
            'volume_ratio': num(x, 'volume_ratio'),
            'turnover_rate': num(x, 'turnover_rate'),
            'book': ((num(x, 'bid_vol') or 0) - (num(x, 'ask_vol') or 0))
                    / max(1.0, (num(x, 'bid_vol') or 0) + (num(x, 'ask_vol') or 0)),
        }
        key = 'auction_turnover' if window == 'auction' else 'open15_turnover'
        med = base.get(key)
        if med and med > 0:
            row['rvol'] = turn / med
        else:
            row['rvol'] = None
            missing.append(key)
        # The liquidity and tick filters are part of the rule the backtest tested,
        # so a name that cannot clear them -- including one whose liquidity is
        # simply unknown -- leaves the board.  A missing relative-volume baseline
        # only costs that factor: it is shown as absent, never as neutral.
        if base.get('day_turnover', 0.0) < MIN_TURNOVER:
            missing.append('illiquid' if 'day_turnover' in base else 'no_liquidity_baseline')
        if tick_bp > MAX_TICK_BP:
            missing.append('wide_tick')
        off_board = ('illiquid', 'no_liquidity_baseline', 'wide_tick')
        row['excluded'] = [m for m in missing if m in off_board]
        row['no_baseline'] = [m for m in missing if m not in off_board]
        out.append(row)
    return out


def rank(rows, window):
    """Ordering only -- the backtest has not licensed a threshold."""
    live = [r for r in rows if not r['excluded']]
    live.sort(key=lambda r: (r['gap'], r['rvol'] or 0.0), reverse=True)
    if window == 'auction':
        return live, [r for r in rows if r['excluded']]
    # after the open, a name trading under its own VWAP is not being chased
    return ([r for r in live if r['vs_vwap'] is not None and r['vs_vwap'] > 0],
            [r for r in live if not (r['vs_vwap'] and r['vs_vwap'] > 0)]
            + [r for r in rows if r['excluded']])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--window', choices=('auction', 'open15'), required=True)
    ap.add_argument('--baseline', default=None, help='json from baseline.py')
    ap.add_argument('--universe', default=None)
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--json', default=None)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=11111)
    a = ap.parse_args()

    import futu as ft
    baseline = json.loads(Path(a.baseline).read_text()) if a.baseline and Path(a.baseline).exists() else {}
    ctx = ft.OpenQuoteContext(host=a.host, port=a.port)
    try:
        if a.universe and Path(a.universe).exists():
            codes = sorted(json.loads(Path(a.universe).read_text(encoding='utf-8')))
        else:
            codes = set()
            for plate in PLATES:
                ret, frame = ctx.get_plate_stock(plate)
                if ret != ft.RET_OK:
                    raise RuntimeError('get_plate_stock %s: %s' % (plate, frame))
                codes |= {str(r['code']) for _, r in frame.iterrows()
                          if str(r.get('stock_type')) == 'STOCK'}
            codes = sorted(codes)
        snap = sweep(ctx, ft, codes)
    finally:
        ctx.close()

    rows = score_rows(snap, baseline, a.window)
    scored, unscored = rank(rows, a.window)
    now = datetime.now(HKT).strftime('%Y-%m-%d %H:%M:%S')
    print('%s HKT  %s  universe %d  ranked %d  baseline for %d names'
          % (now, a.window, len(codes), len(scored), len(baseline)))
    if not baseline:
        print('  ! no rolling baseline: relative-volume factors are absent, not neutral.'
              '  Run baseline.py after the close for at least ten trading days.')
    print('%-4s %-11s %-12s %9s %8s %7s %8s %8s %7s'
          % ('#', 'code', 'name', 'last', 'gap%', 'rvol', 'vsVWAP%', 'turnover', 'tickbp'))
    for i, r in enumerate(scored[:a.top], 1):
        print('%-4d %-11s %-12s %9.3f %8.2f %7s %8s %8.1fM %7.1f'
              % (i, r['code'], r['name'][:11], r['last'], r['gap'] * 100,
                 '-' if r['rvol'] is None else '%.1fx' % r['rvol'],
                 '-' if r['vs_vwap'] is None else '%.2f' % (r['vs_vwap'] * 100),
                 r['turnover'] / 1e6, r['tick_bp']))
    if unscored:
        print('  (%d names without the factors this window needs: %s ...)'
              % (len(unscored), ', '.join(r['code'] for r in unscored[:5])))
    if a.json:
        Path(a.json).write_text(json.dumps(
            {'ts': now, 'window': a.window, 'rows': scored[:a.top]}, ensure_ascii=False, indent=1),
            encoding='utf-8')


if __name__ == '__main__':
    main()
