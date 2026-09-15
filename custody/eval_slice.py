"""Fetch and freeze the real custody eval slice from read-only OpenD.

This produces the **eval/custody** dataset: for each locked job
(underlying / direction / exact contract) it stores the same-day
**underlying 1m** and **option 1m** trade bars pulled once from OpenD history,
plus ``manifest.json`` and ``cases.json``. It is deliberately separate from the
**train/research** ``eval-data-v2`` underlying-proxy slice.

Quota-aware: the CLI fetches each series once and writes CSV + parquet; later
runs read the frozen files (see :class:`custody.offline.OfflineMarket`) instead
of re-requesting OpenD history. Uses ``OpenQuoteContext`` read-only only.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path

from .marketdata import day_bars
from .models import ET
from .offline import CASES_NAME, MANIFEST_NAME
from .opend import DEFAULT_HOST, DEFAULT_PORT

TRADE_DATE = '2026-09-14'

# Locked product cases (must-trade custody): underlying / direction / contract.
CASES = [
    {'symbol': 'US.QQQ', 'direction': 'LONG', 'contract': 'US.QQQ260914C705000', 'trade_date': TRADE_DATE},
    {'symbol': 'US.SKHY', 'direction': 'SHORT', 'contract': 'US.SKHY260918P175000', 'trade_date': TRADE_DATE},
    {'symbol': 'US.BABA', 'direction': 'LONG', 'contract': 'US.BABA260918C109000', 'trade_date': TRADE_DATE},
]


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _bar_frame(bars):
    import pandas as pd
    return pd.DataFrame([{
        'code': bar.code, 'close_time': bar.close_time.isoformat(), 'interval': bar.interval,
        'open': bar.open, 'high': bar.high, 'low': bar.low, 'close': bar.close, 'volume': bar.volume,
    } for bar in bars])


def _write_series(root, kind, code, bars):
    folder = Path(root) / kind
    folder.mkdir(parents=True, exist_ok=True)
    csv_path = folder / (code + '.csv')
    parquet_path = folder / (code + '.parquet')
    frame = _bar_frame(bars)
    frame.to_csv(csv_path, index=False)
    frame.to_parquet(parquet_path, index=False)
    return {
        'code': code, 'kind': kind, 'bar_count': len(bars),
        'first_bar': bars[0].close_time.isoformat() if bars else None,
        'last_bar': bars[-1].close_time.isoformat() if bars else None,
        'csv': str(csv_path.relative_to(root)), 'parquet': str(parquet_path.relative_to(root)),
        'sha256': {'csv': _sha256(csv_path), 'parquet': _sha256(parquet_path)},
    }


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
            series.append(_write_series(root, 'underlying', code, bars))
            logger('underlying %s bars=%d first=%s last=%s' % (
                code, len(bars), series[-1]['first_bar'], series[-1]['last_bar']))
        for case in cases:
            code = case['contract']
            bars = day_bars(market, code, trade_date)
            series.append(_write_series(root, 'option', code, bars))
            logger('option %s bars=%d first=%s last=%s' % (
                code, len(bars), series[-1]['first_bar'], series[-1]['last_bar']))
    finally:
        if own_market:
            market.close()

    (root / CASES_NAME).write_text(json.dumps({'schema_version': 1, 'dataset': 'custody-eval-' + trade_date,
                                               'trade_date': trade_date, 'cases': cases}, indent=2) + '\n')
    manifest = {
        'schema_version': 1,
        'dataset': 'custody-eval-' + trade_date,
        'role': 'eval/custody',
        'trade_date': trade_date,
        'timezone': 'America/New_York',
        'interval': '1m',
        'opend_host': host,
        'opend_port': int(port),
        'fetched_at': datetime.now(ET).isoformat(),
        'session': session,
        'price_note': ('Underlying and option series are OpenD K_1M trade OHLCV, not NBBO. '
                       'Live uses option bid/ask quotes through the same provider boundary.'),
        'train_research_note': 'Separate from eval-data-v2 (underlying-proxy research).',
        'series': series,
        'cases': cases,
    }
    (root / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + '\n')
    _zip_tree(root)
    return manifest


def _zip_tree(root):
    root = Path(root)
    zip_path = root.with_suffix('.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(root.parent))
    return zip_path


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
