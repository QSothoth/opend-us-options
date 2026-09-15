"""Fetch and freeze the real custody eval slice from read-only OpenD.

This produces the **validation/eval** dataset: for each locked job
(underlying / direction / exact contract) it stores the same-day
**underlying 1m** and **option 1m** trade bars pulled once from OpenD history,
plus ``manifest.json`` and ``cases.json``. It is deliberately separate from the
**research/underlying-proxy** ``eval-data-v2`` slice, which has no option path.

Quota-aware: the CLI fetches each series once and writes CSV + parquet; later
runs read the frozen files (see :class:`custody.offline.OfflineMarket`) instead
of re-requesting OpenD history. Uses ``OpenQuoteContext`` read-only only.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .marketdata import day_bars
from .models import ET
from .opend import DEFAULT_HOST, DEFAULT_PORT
from .sliceio import write_series, write_slice_docs, zip_tree

TRADE_DATE = '2026-09-14'

# Locked product cases (must-trade custody): underlying / direction / contract.
CASES = [
    {'symbol': 'US.QQQ', 'direction': 'LONG', 'contract': 'US.QQQ260914C705000', 'trade_date': TRADE_DATE},
    {'symbol': 'US.SKHY', 'direction': 'SHORT', 'contract': 'US.SKHY260918P175000', 'trade_date': TRADE_DATE},
    {'symbol': 'US.BABA', 'direction': 'LONG', 'contract': 'US.BABA260918C109000', 'trade_date': TRADE_DATE},
]


def fetch_slice(out_dir, trade_date=TRADE_DATE, cases=None, market=None, host=DEFAULT_HOST,
                port=DEFAULT_PORT, calendar=None, logger=print):
    """Pull the six series once, write frozen files + manifest + cases, zip it."""
    cases = list(cases or CASES)
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    own_market = market is None
    if own_market:
        from .opend import OpenDMarket
        market = OpenDMarket(host=host, port=port)
    try:
        session = None
        if calendar is not None:
            exchange = calendar.session(trade_date)
            session = {'open': exchange.opens.isoformat(), 'close': exchange.closes.isoformat()}
        series = []
        for code in sorted({case['symbol'] for case in cases}):
            bars = day_bars(market, code, trade_date)
            series.append(write_series(root, 'underlying', code, bars))
            logger('underlying %s bars=%d first=%s last=%s' % (
                code, len(bars), series[-1]['first_bar'], series[-1]['last_bar']))
        for case in cases:
            code = case['contract']
            bars = day_bars(market, code, trade_date)
            series.append(write_series(root, 'option', code, bars))
            logger('option %s bars=%d first=%s last=%s' % (
                code, len(bars), series[-1]['first_bar'], series[-1]['last_bar']))
    finally:
        if own_market:
            market.close()

    cases_doc = {'schema_version': 1, 'dataset': 'custody-eval-' + trade_date,
                 'role': 'eval/custody', 'trade_date': trade_date, 'cases': cases}
    manifest = {
        'schema_version': 1,
        'dataset': 'custody-eval-' + trade_date,
        'role': 'eval/custody',
        'role_note': 'validation/eval for the custody product',
        'trade_date': trade_date,
        'timezone': 'America/New_York',
        'interval': '1m',
        'paired': True,
        'required_series': ['underlying', 'option'],
        'success_metric': 'option_pnl',
        'option_multiplier': 100,
        'opend_host': host,
        'opend_port': int(port),
        'fetched_at': datetime.now(ET).isoformat(),
        'session': session,
        'price_note': ('Underlying and option series are OpenD K_1M trade OHLCV, not NBBO. '
                       'Live uses option bid/ask quotes through the same provider boundary.'),
        'metric_note': ('Custody success is measured on the option contract path/fills only; '
                        'underlying-proxy payoff is forbidden.'),
        'train_research_note': ('Separate from eval-data-v2 (underlying-proxy research); '
                                'v2 cannot be custody train because it has no option series.'),
        'series': series,
        'cases': cases,
    }
    write_slice_docs(root, cases_doc, manifest)
    zip_tree(root)
    return manifest


def build_argument_parser():
    import argparse
    parser = argparse.ArgumentParser(prog='custody fetch-eval',
                                     description='Fetch the frozen custody eval slice from read-only OpenD (once).')
    parser.add_argument('--out', required=True, help='output directory, e.g. out/custody-eval-2026-09-14')
    parser.add_argument('--date', default=TRADE_DATE, help='ET trade date (default %s)' % TRADE_DATE)
    parser.add_argument('--host', default=DEFAULT_HOST)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    return parser


def main(argv=None):
    args = build_argument_parser().parse_args(argv)
    from .opend import OpenDMarket, OpenDTradingCalendar
    market = OpenDMarket(host=args.host, port=args.port)
    try:
        calendar = OpenDTradingCalendar(market)
        manifest = fetch_slice(args.out, trade_date=args.date, market=market, host=args.host,
                               port=args.port, calendar=calendar)
    finally:
        market.close()
    print(json.dumps({'dataset': manifest['dataset'], 'series': [
        {'code': item['code'], 'kind': item['kind'], 'bar_count': item['bar_count']}
        for item in manifest['series']]}, indent=2))
    return 0
