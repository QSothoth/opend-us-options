"""Replay either frozen v2 candidate on the training Release, offline only."""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from custody.baseline import replay_case, session_for, summarize
from custody.marketdata import require_paired_bars
from custody.offline import OfflineMarket, assert_paired_slice
from custody.registry import Registry

STRATEGIES = ('custody_payoff_1m_v2', 'custody_payoff_aggressive_1m_v2')

from data_boundary import training_slice

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--train', required=True)
    ap.add_argument('--strategy', choices=STRATEGIES, default=STRATEGIES[0])
    ap.add_argument('--delay', type=int, choices=(1, 2, 3), default=1)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    manifest, cases, checked = training_slice(args.train)
    strategy = Registry().get(args.strategy)
    market = OfflineMarket(args.train, prefer_csv=True)
    rows = []
    for i, case in enumerate(cases):
        underlying, option = require_paired_bars(market, case['symbol'], case['contract'], case['trade_date'])
        rows.append(replay_case(case, underlying, option, session_for(case, manifest),
                                delay_minutes=args.delay, strategy_override=strategy))
        if (i + 1) % 40 == 0:
            print(f'{i + 1}/124', flush=True)
    report = dict(strategy_id=args.strategy, strategy_sha256=strategy['sha256'],
                  checksums_verified=checked, real_fills=False, orders_never_submitted=True,
                  validation_used=False, fill_delay_minutes=args.delay,
                  fill_model='next positive-volume option close; OHLCV simulation, not bid/ask fills',
                  summary=summarize(rows), cases=rows)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps(report['summary']['primary'], indent=2))
    if not all(r['one_round_trip'] for r in rows):
        raise SystemExit('Incomplete case: report saved, full-cohort success not claimed')

if __name__ == '__main__':
    main()
