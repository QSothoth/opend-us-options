"""Minimal offline custody eval entrypoint (data plumbing + must-trade stub).

This loads the three locked custody cases from the frozen eval slice and feeds
the same-day **underlying 1m** and **option 1m** series through the shared
:class:`~custody.marketdata.MarketDataProvider` boundary. It needs **no**
multi-day warmup and runs **no** research gates (``orb_rvol_rsi_1m_v1`` is not
the custody success criterion).

The timing policy here (:class:`DeadlineFallbackPolicy`) is an explicitly
labelled placeholder: it records a single entry and a single flatten exit so the
must-trade plumbing is exercised. It is **not** an alpha and its fills are the
real option 1m bar closes, not NBBO. Live would use option bid/ask quotes.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from .marketdata import day_bars
from .models import ET
from .offline import OfflineMarket, load_cases, load_manifest

DEFAULT_ENTRY_AT = '10:00'
DEFAULT_FLATTEN_BEFORE_CLOSE_MINUTES = 15

NOTE = ('placeholder must-trade plumbing only; option fills are frozen 1m bar closes, '
        'not NBBO; not an alpha and not the research gate strategy')


@dataclass(frozen=True)
class Fill:
    at: str
    price: float
    basis: str


@dataclass(frozen=True)
class DeadlineFallbackPolicy:
    """Clearly-marked placeholder must-trade timing.

    Enters at the first non-zero option bar at/after ``entry_at`` and exits at
    the last non-zero option bar at/before the flatten deadline
    (``session_close - flatten_before_close_minutes``). Exactly one round trip.
    """

    entry_at: str = DEFAULT_ENTRY_AT
    flatten_before_close_minutes: int = DEFAULT_FLATTEN_BEFORE_CLOSE_MINUTES

    def run(self, case, underlying_bars, option_bars, session_close):
        day = case['trade_date']
        entry_target = datetime.combine(date.fromisoformat(day),
                                        dtime.fromisoformat(self.entry_at), tzinfo=ET)
        close = session_close.astimezone(ET)
        flatten = close - timedelta(minutes=self.flatten_before_close_minutes)
        tradable = [bar for bar in option_bars if bar.close > 0]
        after = [bar for bar in tradable if bar.close_time >= entry_target]
        entry_bar = after[0] if after else None
        before = [bar for bar in tradable if bar.close_time <= flatten and
                  (entry_bar is None or bar.close_time >= entry_bar.close_time)]
        # Must-trade placeholder: never sit flat, even if only the entry bar is
        # available before the flatten deadline.
        exit_bar = before[-1] if before else entry_bar
        entry = Fill(entry_bar.close_time.isoformat(), entry_bar.close,
                     'option_bar_close') if entry_bar else None
        exit_ = Fill(exit_bar.close_time.isoformat(), exit_bar.close,
                     'option_bar_close') if exit_bar else None
        one_round_trip = bool(entry and exit_ and exit_.at >= entry.at)
        return {
            'case': case,
            'underlying_bar_count': len(underlying_bars),
            'option_bar_count': len(option_bars),
            'session_close': close.isoformat(),
            'entry': asdict(entry) if entry else None,
            'exit': asdict(exit_) if exit_ else None,
            'one_round_trip': one_round_trip,
            'note': NOTE,
        }


def _default_session_close(day):
    return datetime.combine(date.fromisoformat(day), dtime(16, 0), tzinfo=ET)


def eval_session(root, out=None, provider=None, entry_at=DEFAULT_ENTRY_AT):
    """Run the placeholder must-trade plumbing over every frozen custody case."""
    root = Path(root)
    manifest = load_manifest(root)
    cases_doc = load_cases(root)
    cases = cases_doc.get('cases', [])
    provider = provider or OfflineMarket(root)
    session_close = None
    if manifest.get('session') and manifest['session'].get('close'):
        session_close = datetime.fromisoformat(manifest['session']['close'])
    policy = DeadlineFallbackPolicy(entry_at=entry_at)
    results = []
    for case in cases:
        underlying = day_bars(provider, case['symbol'], case['trade_date'])
        option = day_bars(provider, case['contract'], case['trade_date'])
        close = session_close or _default_session_close(case['trade_date'])
        results.append(policy.run(case, underlying, option, close))
    report = {
        'dataset': manifest.get('dataset'),
        'role': 'eval/custody',
        'provider': type(provider).__name__,
        'entry_at': entry_at,
        'cases': results,
    }
    if out:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + '\n')
    return report


def build_argument_parser():
    parser = argparse.ArgumentParser(
        prog='custody eval-session',
        description='Replay the frozen custody eval slice through the shared provider (no warmup, no gates).')
    parser.add_argument('--slice', required=True, help='custody eval slice directory')
    parser.add_argument('--out', default=None, help='optional JSON report path')
    parser.add_argument('--entry-at', default=DEFAULT_ENTRY_AT, help='placeholder entry time ET (default 10:00)')
    parser.add_argument('--live', action='store_true', help='read the same interface live from OpenD instead of files')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=11111)
    return parser


def main(argv=None):
    args = build_argument_parser().parse_args(argv)
    market = None
    provider = None
    if args.live:
        from .opend import OpenDMarket
        market = OpenDMarket(host=args.host, port=args.port)
        provider = market
    try:
        report = eval_session(args.slice, out=args.out, provider=provider, entry_at=args.entry_at)
    finally:
        if market is not None:
            market.close()
    print(json.dumps({'dataset': report['dataset'], 'provider': report['provider'], 'cases': [
        {'symbol': item['case']['symbol'], 'direction': item['case']['direction'],
         'contract': item['case']['contract'], 'underlying_bars': item['underlying_bar_count'],
         'option_bars': item['option_bar_count'], 'one_round_trip': item['one_round_trip'],
         'entry': item['entry'], 'exit': item['exit']} for item in report['cases']]}, indent=2))
    return 0
