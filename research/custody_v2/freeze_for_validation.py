"""Freeze both registered strategies after complete pinned-training replays."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from data_boundary import REPO, PINS, digest, load_training_cache
from custody.registry import Registry

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--train',required=True)
    ap.add_argument('--training-reports',nargs=2,required=True)
    ap.add_argument('--out',required=True)
    a=ap.parse_args();out=Path(a.out)
    if out.exists():raise ValueError('freeze already exists; refusing overwrite')
    *_,meta=load_training_cache(a.train,Path(__file__).parent/'results')
    key=lambda c:(c['contract'],c['trade_date'])
    expected={key(c) for c in meta['cases']}
    wanted=('custody_payoff_aggressive_1m_v2','custody_payoff_1m_v2')
    strategies={sid:Registry().get(sid)['sha256'] for sid in wanted};seen=set();receipts={}
    for name in a.training_reports:
        p=Path(name);raw=p.read_bytes();r=json.loads(raw);sid=r['strategy_id']
        if (sid not in strategies or sid in seen or r['strategy_sha256']!=strategies[sid]
                or len(r['cases'])!=124 or {key(c['case']) for c in r['cases']}!=expected
                or not all(c['one_round_trip'] for c in r['cases']) or r['validation_used']):
            raise ValueError('complete pinned-training replay required for each frozen strategy')
        seen.add(sid);receipts[sid]=digest(raw)
    paths=sorted(set(list((REPO/'custody').glob('*.py'))+list((REPO/'custody/strategies').glob('*.json'))
             +list((REPO/'custody/tests').glob('*.py'))+list(Path(__file__).parent.glob('*.py'))+[PINS]))
    frozen={'created_at_utc':datetime.now(timezone.utc).isoformat(),'stage':'after training; before fixed validation replay',
      'source_sha256':{str(p.relative_to(REPO)):digest(p.read_bytes()) for p in paths},
      'strategy_sha256':strategies,'training_report_sha256':receipts,'data_pins_sha256':digest(PINS.read_bytes()),
      'validation_dataset':'custody-eval-2026-09-14','selection_on_validation':False}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(frozen,indent=2)+'\n');print(out)
if __name__=='__main__':main()
