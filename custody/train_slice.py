"""Build the real paired custody **train** starter from read-only OpenD.

The custody product needs the same layout for train and validation: for each
case, a same-day **underlying 1m** series for timing plus the exact traded
**option 1m** series. ``eval-data-v2`` is underlying-only research leftover and
is refused by the custody metric path, so it cannot serve as custody train.

This builder is quota-aware: preference is a small set of liquid underlyings and
a handful of recent sessions, each series fetched once and frozen to
``underlying/`` + ``option/`` + ``cases.json`` + ``manifest.json`` (optionally
zipped). It uses a read-only ``OpenQuoteContext`` (option chain + history) and
never touches a trade API.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .marketdata import MissingCustodyPairError, day_bars
from .models import ET
from .opend import DEFAULT_HOST, DEFAULT_PORT
from .sliceio import write_series, write_slice_docs, zip_tree

# A small, fixed real train starter: recent liquid sessions x liquid underlyings.
TRAIN_TAG = 'custody-train-2026-09-08_11'
TRAIN_SESSIONS = ['2026-09-08', '2026-09-09', '2026-09-10', '2026-09-11']
TRAIN_UNDERLYINGS = ['US.SPY', 'US.QQQ', 'US.AAPL']
# A still-listed short-dated expiry so history exists for every train session.
TRAIN_EXPIRY = '2026-09-18'
DIRECTIONS = {'US.SPY': 'LONG', 'US.QQQ': 'SHORT', 'US.AAPL': 'LONG'}


def _strike(row):
    value = row.get('strike_price')
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(bars):
    dedup = {bar.close_time: bar for bar in bars}
    return [dedup[key] for key in sorted(dedup)]


def nearest_contract(rows, spot, right):
    """Return ``(code, strike)`` for the listed strike nearest ``spot``.

    ``rows`` are OpenD option-chain rows; ``right`` is ``'CALL'`` or ``'PUT'``.
    Never guesses a code from text and never falls back to a synthetic strike.
    """
    spot = float(spot)
    best = None
    for row in rows:
        if not hasattr(row, 'get'):
            continue
        row_right = str(row.get('option_type', '')).strip().upper()
        if right and row_right and row_right != right:
            continue
        code = str(row.get('code', '')).strip().upper()
        strike = _strike(row)
        if not code or strike is None or strike <= 0:
            continue
        distance = abs(strike - spot)
        if best is None or distance < best[0]:
            best = (distance, code, strike)
    if best is None:
        raise ValueError('no listed %s contract found for spot %.4f' % (right, spot))
    return best[1], best[2]


def build_train_slice(out_dir, sessions=None, underlyings=None, expiry=TRAIN_EXPIRY,
                      market=None, host=DEFAULT_HOST, port=DEFAULT_PORT, logger=print):
    """Fetch a small real paired train starter and freeze it in eval-slice layout.

    Each OpenD series is fetched exactly once (quota-aware). Series that span
    several sessions (e.g. one underlying across the train week) are concatenated
    into a single code file so the on-disk layout matches the eval slice exactly;
    :class:`custody.offline.OfflineMarket` already filters by session date.
    """
    sessions = list(sessions or TRAIN_SESSIONS)
    underlyings = list(underlyings or TRAIN_UNDERLYINGS)
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    own_market = market is None
    if own_market:
        from .opend import OpenDMarket
        market = OpenDMarket(host=host, port=port)
    if not hasattr(market, 'option_chain'):
        if own_market:
            market.close()
        raise ValueError('train builder requires a provider with option_chain()')
    accumulated = {}
    chain_cache = {}

    def chain_for(symbol, right):
        # The listed chain for a fixed (underlying, expiry, right) is constant
        # across sessions; cache it so OpenD's option-chain rate limit (10/30s)
        # is never approached. History quota is likewise spent once per series.
        key = (symbol.upper(), right)
        if key not in chain_cache:
            chain_cache[key] = market.option_chain(symbol, expiry, right)
        return chain_cache[key]

    def add(kind, code, bars):
        accumulated.setdefault((kind, code), []).extend(bars)

    cases = []
    try:
        for session in sessions:
            for symbol in underlyings:
                direction = DIRECTIONS.get(symbol.upper(), 'LONG')
                right = 'CALL' if direction == 'LONG' else 'PUT'
                underlying_bars = day_bars(market, symbol, session)
                if not underlying_bars:
                    raise MissingCustodyPairError('no underlying 1m bars for %s on %s' % (symbol, session))
                reference = underlying_bars[0].open
                contract, strike = nearest_contract(
                    chain_for(symbol, right), reference, right)
                option_bars = day_bars(market, contract, session)
                if not option_bars:
                    raise MissingCustodyPairError(
                        'no option 1m bars for %s on %s (paired train case)' % (contract, session))
                add('underlying', symbol, underlying_bars)
                add('option', contract, option_bars)
                cases.append({
                    'symbol': symbol, 'direction': direction, 'contract': contract,
                    'trade_date': session, 'expiry': expiry, 'right': right,
                    'strike': strike, 'reference_price': reference,
                    'dte': (date.fromisoformat(expiry) - date.fromisoformat(session)).days,
                    'underlying_bar_count': len(underlying_bars),
                    'option_bar_count': len(option_bars),
                })
                logger('case %s %s %s %s under=%d option=%d' % (
                    session, symbol, direction, contract, len(underlying_bars), len(option_bars)))
    finally:
        if own_market:
            market.close()

    series = []
    for (kind, code), bars in accumulated.items():
        series.append(write_series(root, kind, code, _dedupe(bars)))

    dataset = TRAIN_TAG
    cases_doc = {'schema_version': 1, 'dataset': dataset, 'role': 'train/custody',
                 'paired': True, 'required_series': ['underlying', 'option'],
                 'sessions': sessions, 'underlyings': underlyings, 'cases': cases}
    manifest = {
        'schema_version': 1,
        'dataset': dataset,
        'role': 'train/custody',
        'role_note': 'real paired custody train starter (underlying 1m timing + option 1m fills)',
        'timezone': 'America/New_York',
        'interval': '1m',
        'paired': True,
        'required_series': ['underlying', 'option'],
        'success_metric': 'option_pnl',
        'option_multiplier': 100,
        'opend_host': host,
        'opend_port': int(port),
        'fetched_at': datetime.now(ET).isoformat(),
        'sessions': sessions,
        'underlyings': underlyings,
        'expiry': expiry,
        'contract_selection': ('nearest listed strike to the session first-bar underlying open; '
                               'right = CALL for LONG / PUT for SHORT'),
        'price_note': ('Underlying and option series are OpenD K_1M trade OHLCV, not NBBO. '
                       'Live uses option bid/ask quotes through the same provider boundary.'),
        'metric_note': ('Custody success is measured on the option contract path/fills only; '
                        'underlying-proxy payoff is forbidden.'),
        'validation_reference': 'custody-eval-2026-09-14',
        'series': series,
        'cases': cases,
    }
    write_slice_docs(root, cases_doc, manifest)
    zip_tree(root)
    return manifest


def build_argument_parser():
    import argparse
    parser = argparse.ArgumentParser(
        prog='custody fetch-train',
        description='Fetch a small real paired custody train starter from read-only OpenD (quota-aware).')
    parser.add_argument('--out', required=True, help='output directory, e.g. out/custody-train-2026-09-08_11')
    parser.add_argument('--sessions', default=','.join(TRAIN_SESSIONS),
                        help='comma-separated ET trade dates (default %s)' % ','.join(TRAIN_SESSIONS))
    parser.add_argument('--underlyings', default=','.join(TRAIN_UNDERLYINGS),
                        help='comma-separated underlyings (default %s)' % ','.join(TRAIN_UNDERLYINGS))
    parser.add_argument('--expiry', default=TRAIN_EXPIRY, help='listed option expiry (default %s)' % TRAIN_EXPIRY)
    parser.add_argument('--host', default=DEFAULT_HOST)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    return parser


def main(argv=None):
    args = build_argument_parser().parse_args(argv)
    from .opend import OpenDMarket
    market = OpenDMarket(host=args.host, port=args.port)
    try:
        manifest = build_train_slice(
            args.out,
            sessions=[s.strip() for s in args.sessions.split(',') if s.strip()],
            underlyings=[s.strip() for s in args.underlyings.split(',') if s.strip()],
            expiry=args.expiry, market=market, host=args.host, port=args.port)
    finally:
        market.close()
    print(json.dumps({'dataset': manifest['dataset'], 'role': manifest['role'], 'cases': [
        {'trade_date': c['trade_date'], 'symbol': c['symbol'], 'direction': c['direction'],
         'contract': c['contract'], 'strike': c['strike'], 'reference_price': c['reference_price']}
        for c in manifest['cases']]}, indent=2))
    return 0
