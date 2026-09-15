"""Confirm the frozen selection with CustodyService; training Release only."""
from data_boundary import training_slice, load_training_cache
import argparse,hashlib,json,sys,time
from pathlib import Path
from datetime import datetime
import numpy as np
from search import REPO,evaluate,stats
from custody.adaptive import make_strategy
from custody.baseline import replay_case,session_for,summarize
from custody.offline import OfflineMarket
from custody.marketdata import require_paired_bars

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--train',required=True);a=ap.parse_args()
    out=Path(__file__).parent/'results';selected=json.loads((out/'FINAL_SELECTION_BEFORE_CONFIRMATION.json').read_text())
    if 'custody-eval' in a.train:raise ValueError('holdout forbidden')
    manifest,cases,checked=training_slice(a.train)
    fs,pp,nb,dte,blocks,_=load_training_cache(a.train,out)
    if manifest['role']!='train/custody' or len(cases)!=124:raise ValueError('train124 required')
    strategy=make_strategy(selected['profile'],selected['e'],selected['x'],'custody_payoff_1m_v2')
    path=REPO/'custody/strategies/custody_payoff_1m_v2.json'
    path.write_text(json.dumps(strategy['config'],indent=2)+'\n')
    indexpath=REPO/'custody/strategies/index.json';index=json.loads(indexpath.read_text())
    index['strategies'][strategy['strategy_id']]={k:v for k,v in strategy.items() if k not in ('strategy_id','config')}
    index['strategies'][strategy['strategy_id']].update(file=path.name,status='trained_candidate_dryrun',description='Train-only payoff-first search winner; frozen before service confirmation')
    indexpath.write_text(json.dumps(index,indent=2)+'\n')
    frozen={'selected_id':selected['id'],'strategy':strategy['strategy_id'],'strategy_sha256':strategy['sha256'],
       'selection_file_sha256':hashlib.sha256((out/'FINAL_SELECTION_BEFORE_CONFIRMATION.json').read_bytes()).hexdigest(),
       'validation_used':False,'source_sha256':{str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((REPO/'custody').glob('*.py'))}}
    (out/'FROZEN_BEFORE_SERVICE_REPLAY.json').write_text(json.dumps(frozen,indent=2))
    market=OfflineMarket(a.train,prefer_csv=True)
    datasets=[]
    for c in cases:
        u,o=require_paired_bars(market,c['symbol'],c['contract'],c['trade_date']);datasets.append((c,u,o,session_for(c,manifest)))
    runs={};parity=[]
    for name,override,delay in [('v1',None,1),('v2',strategy,1),('v2_cold_restart',strategy,1),('v2_delay2',strategy,2),('v2_delay3',strategy,3)]:
        rows=[];started=time.monotonic()
        fast=evaluate(fs[selected['profile']],pp,nb,dte,np.array(selected['e'],float),np.array(selected['x'],float),delay) if override else None
        for i,(c,u,o,session) in enumerate(datasets):
            row=replay_case(c,u,o,session,delay_minutes=delay,strategy_override=override);rows.append(row)
            if override:
                if not row['one_round_trip']:raise AssertionError((name,c,'incomplete'))
                for col,side,field in [(0,'entry','signal_at'),(1,'entry','at'),(2,'exit','decision_at'),(3,'exit','signal_at'),(4,'exit','at')]:
                    actual=(datetime.fromisoformat(row[side][field])-session.opens).total_seconds()/60
                    if actual!=fast[i,col]:raise AssertionError((name,c,col,actual,fast[i,col]))
                if abs(row['entry']['price']-fast[i,5])>1e-10 or abs(row['exit']['price']-fast[i,6])>1e-10:raise AssertionError('price mismatch')
            if (i+1)%40==0:print(name,i+1,'/124',round(time.monotonic()-started,1),'seconds',flush=True)
        runs[name]={'summary':summarize(rows),'cases':rows}
        if override:parity.append({'run':name,'cases':len(rows),'exact_timestamp_and_price_matches':len(rows)})
        (out/(name+'_service_report.json')).write_text(json.dumps(runs[name],indent=2))
    old=json.loads((REPO/'custody/baselines/v1/train_report.json').read_text())['variants']['baseline']['cases']
    def record(r):return [r['entry'][k] for k in ('signal_at','at','price')]+[r['exit'][k] for k in ('signal_at','at','price')]
    assert [record(r) for r in old]==[record(r) for r in runs['v1']['cases']]
    assert runs['v2']==runs['v2_cold_restart']
    proof={'original_v1_124_trades_unchanged':True,'two_cold_service_runs_identical':True,'fast_service_parity':parity,'no_broker_orders':True,'validation_used':False}
    (out/'SERVICE_VERIFICATION.json').write_text(json.dumps(proof,indent=2))
    summary={k:v['summary']['primary'] for k,v in runs.items()}
    (out/'service_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary),flush=True)

if __name__=='__main__':main()
