"""Report both already-published frozen v2 candidates once on the fixed eval Release.
No search, selection, fitting or changes to strategy parameters are performed.
"""
import argparse, hashlib, json, sys
from pathlib import Path
REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO))
from custody.baseline import verify_freeze,replay_case,session_for,summarize
from custody.registry import Registry
from custody.offline import OfflineMarket
from custody.marketdata import require_paired_bars
from data_boundary import verify_release

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--slice',required=True);ap.add_argument('--out',required=True)
    ap.add_argument('--freeze',required=True,help='source freeze created after training and before validation')
    a=ap.parse_args()
    freeze=Path(a.freeze)
    frozen=verify_freeze(freeze)
    if Path(a.out).parent.name != 'validation_only':
        raise ValueError('validation reports must be written to a validation_only directory')
    if Path(a.out).exists():
        raise ValueError('refusing to overwrite an existing validation report')
    strategies=[Registry().get(s) for s in ('custody_payoff_aggressive_1m_v2','custody_payoff_1m_v2')]
    for strategy in strategies:
        if frozen.get('strategy_sha256', {}).get(strategy['strategy_id']) != strategy['sha256']:
            raise ValueError('strategy missing from frozen evaluation plan')
    manifest,cases,checked=verify_release(a.slice,'validation')
    if manifest.get('role')=='train/custody' or len(cases)!=3 or Path(a.slice).name!='custody-eval-2026-09-14':
        raise ValueError('Expected exact fixed 3-case evaluation Release')
    market=OfflineMarket(a.slice,prefer_csv=True)
    datasets=[(c,*require_paired_bars(market,c['symbol'],c['contract'],c['trade_date']),session_for(c,manifest)) for c in cases]
    report={'strategy_origin_commit':'783f92c91b4f30bed46e2595ab3889c60a582b30','freeze_sha256':hashlib.sha256(freeze.read_bytes()).hexdigest(),
       'dataset':manifest['dataset'],'checksums_verified':checked,'fitting_or_selection':False,'orders_submitted':False,
       'status':'fixed evaluation set; previously reported for v1; not newly unseen data',
       'fill_model':'first positive-volume option close at least 1 minute after intent; same frozen training model',
       'strategies':{}}
    for strategy in strategies:
        rows=[replay_case(c,u,o,session,delay_minutes=1,strategy_override=strategy) for c,u,o,session in datasets]
        report['strategies'][strategy['strategy_id']]={'sha256':strategy['sha256'],'summary':summarize(rows),'cases':rows}
    p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    for sid,r in report['strategies'].items():
        print(sid);print(json.dumps(r['summary'],ensure_ascii=False)[:10000])
        for row in r['cases']:print(json.dumps({'case':row['case'],'entry':row.get('entry'),'exit':row.get('exit'),'pnl':row.get('option_pnl')},ensure_ascii=False))
if __name__=='__main__':main()
