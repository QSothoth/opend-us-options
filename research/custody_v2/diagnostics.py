"""Training-only final diagnostics, no promotion from post-selection ablations."""
import argparse,copy,hashlib,json
from pathlib import Path
from datetime import datetime
import numpy as np
from numba import njit
from search import evaluate,stats,fast_exit,REPO
from custody.adaptive import make_strategy,DEFAULT_EXIT,validate_vectors
from custody.baseline import replay_case,verify_slice,session_for,summarize
from custody.offline import OfflineMarket
from custody.marketdata import require_paired_bars

@njit(cache=True)
def transplanted(features,prices,nb,dtes,entry_intents,x,delay=1):
    out=np.full((len(dtes),8),-1.)
    for c in range(len(dtes)):
        es=int(entry_intents[c]);ef=nb[c,es+delay]
        if ef>=390:continue
        ep=features[c,ef,1];atr=features[c,es,2];state=np.array([ep,float(ef),0.,-1e100])
        for t in range(ef,390):
            reason=7 if t>=x[16] else fast_exit(features[c,t],x,state,ep,ef,atr,dtes[c])
            if reason:
                intent=nb[c,t];fill=nb[c,min(390,intent+delay)]
                if fill<390:out[c]=np.array([es,ef,t,intent,fill,prices[c,ef],prices[c,fill],reason],dtype=np.float64)
                break
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--train',required=True);a=ap.parse_args()
    out=Path(__file__).parent/'results';z=np.load(out/'cache.npz')
    fs,pp,nb,dtes,blocks=[z[k] for k in ('features','prices','next_bar','dtes','blocks')]
    meta=json.loads((out/'cache_metadata.json').read_text());cases=meta['cases']
    selected=json.loads((out/'FINAL_SELECTION_BEFORE_CONFIRMATION.json').read_text())
    nominal=json.loads((out/'NOMINAL_CHAMPION.json').read_text())
    def ev(c,delay=1):return evaluate(fs[c['profile']],pp,nb,dtes,np.array(c['e'],float),np.array(c['x'],float),delay)
    diagnostics={}
    # Freeze concrete ablations before computing outcomes; these are diagnostic,
    # not a new hidden selection round.
    plans=[]
    changes=[('no_soft','x',1,0),('soft_always','x',3,0),('no_failure','x',4,0),('no_trail','x',9,0),
      ('fixed_entry_atr_trail','x',20,0),('wide_trail4','x',9,4),('no_dte_clock','x',18,1),
      ('fallback1030','e',2,60),('fallback1100','e',2,90),('no_volume','e',8,0),('neutral_rsi','e',6,0),
      ('no_rsi','e',6,-50),('flatten1545','x',16,375)]
    for name,side,k,v in changes:
        c=copy.deepcopy(selected);c[side][k]=v;validate_vectors(c['e'],c['x']);plans.append(dict(name=name,profile=c['profile'],e=c['e'],x=c['x']))
    (out/'ABLATIONS_BEFORE_RESULTS.json').write_text(json.dumps(plans,indent=2))
    base=ev(selected);ablations=[]
    for c in plans:
        tr=ev(c);ablations.append(dict(name=c['name'],changed_cases=int(np.any(tr!=base,axis=1).sum()),**stats(tr,blocks)))
    diagnostics['ablations']=ablations
    v1=json.loads((out/'v1_service_report.json').read_text())['cases']
    t1=np.array([(datetime.fromisoformat(r['entry']['signal_at'])-datetime.fromisoformat(r['entry']['signal_at']).replace(hour=9,minute=30)).total_seconds()/60 for r in v1])
    t2=base[:,0]
    matrix={}
    for name,profile,entries,x in [('v1_entry_v1_exit',1,t1,DEFAULT_EXIT),('v2_entry_v1_exit',1,t2,DEFAULT_EXIT),
                                    ('v1_entry_v2_exit',selected['profile'],t1,selected['x']),('v2_entry_v2_exit',selected['profile'],t2,selected['x'])]:
        tr=transplanted(fs[profile],pp,nb,dtes,entries,np.array(x,float))
        matrix[name]={'gross':stats(tr,blocks,0,0),'net':stats(tr,blocks)}
        if name=='v1_entry_v1_exit':
            assert all(abs(tr[i,5]-r['entry']['price'])<1e-10 and abs(tr[i,6]-r['exit']['price'])<1e-10 for i,r in enumerate(v1))
        if name=='v2_entry_v2_exit':assert np.array_equal(tr,base)
    diagnostics['factorial']=matrix
    neighbors=[];seen=set()
    # Only active numeric parameters of the final selected rule.
    for side,indices in [('e',[1,2,6,8]),('x',[0,1,2,3,4,6,8,9,18])]:
        for k in indices:
            for factor in (.8,1.2):
                c=copy.deepcopy(selected);v=c[side][k]*factor
                if (side=='e' and k in (1,2)) or (side=='x' and k in (2,4,6)):v=round(v)
                if v==c[side][k]:continue
                c[side][k]=v
                try:validate_vectors(c['e'],c['x'])
                except ValueError:continue
                key=json.dumps([c['e'],c['x']])
                if key in seen:continue
                seen.add(key);tr=ev(c)
                neighbors.append(dict(side=side,index=k,value=v,changed_cases=int(np.any(tr!=base,axis=1).sum()),**stats(tr,blocks)))
    diagnostics['neighbors']=neighbors
    diagnostics['neighbor_summary']={'count':len(neighbors),'complete':sum(r['completed']==124 for r in neighbors),
          'positive_net_both_units':sum(r['eligible'] for r in neighbors),'dual_payoff_range':[min(r['dual_payoff'] for r in neighbors),max(r['dual_payoff'] for r in neighbors)]}
    for label,c in [('robust',selected),('aggressive',nominal)]:
        tr=ev(c);paid=tr[:,5]*1.0025*100;pnl=tr[:,6]*.9975*100-paid-1.3;returns=pnl/paid
        by={}
        for field,values in [('symbol',[c['symbol'] for c in cases]),('date',[c['trade_date'] for c in cases]),('dte',list(dtes)),('direction',[c['direction'] for c in cases])]:
            by[field]={str(v):stats(tr[np.array(values)==v],blocks[np.array(values)==v]) for v in sorted(set(values))}
        loo={str(v):stats(tr[np.array([c['symbol'] for c in cases])!=v],blocks[np.array([c['symbol'] for c in cases])!=v]) for v in sorted({c['symbol'] for c in cases})}
        bootstrap=[];delta=[];rng=np.random.default_rng(20260915)
        original=np.array([(r['exit']['price']*.9975-r['entry']['price']*1.0025)*100-1.3 for r in v1])/np.array([r['entry']['price']*1.0025*100 for r in v1])
        dates=sorted({c['trade_date'] for c in cases});groups=[np.flatnonzero(np.array([c['trade_date'] for c in cases])==d) for d in dates]
        for _ in range(2000):
            ix=np.concatenate([groups[j] for j in rng.integers(0,len(groups),len(groups))]);bootstrap.append(float(returns[ix].mean()));delta.append(float((returns-original)[ix].mean()))
        order=np.argsort(pnl)[::-1]
        diagnostics[label]={'gross':stats(tr,blocks,0,0),'net':stats(tr,blocks),'by':by,'leave_one_symbol_out':loo,
          'without_top1':float(pnl.sum()-pnl[order[0]]),'without_top3':float(pnl.sum()-pnl[order[:3]].sum()),
          'date_bootstrap_mean95':np.quantile(bootstrap,[.025,.975]).tolist(),'date_bootstrap_paired_difference95':np.quantile(delta,[.025,.975]).tolist(),
          'bootstrap_status':'post-selection training description only; not unbiased out-of-sample confidence'}
    (out/'DIAGNOSTICS.json').write_text(json.dumps(diagnostics,indent=2))
    # Retain payoff champion as a separately addressable dryrun candidate too.
    strategy=make_strategy(nominal['profile'],nominal['e'],nominal['x'],'custody_payoff_aggressive_1m_v2')
    p=REPO/'custody/strategies/custody_payoff_aggressive_1m_v2.json';p.write_text(json.dumps(strategy['config'],indent=2)+'\n')
    ip=REPO/'custody/strategies/index.json';index=json.loads(ip.read_text());index['strategies'][strategy['strategy_id']]={k:v for k,v in strategy.items() if k not in ('strategy_id','config')}
    index['strategies'][strategy['strategy_id']].update(file=p.name,status='trained_candidate_dryrun',description='Highest primary net dual-payoff in searched space; more latency-sensitive')
    ip.write_text(json.dumps(index,indent=2)+'\n')
    if 'custody-eval' in a.train:raise ValueError('holdout forbidden')
    manifest,actualcases,_=verify_slice(a.train);assert manifest['role']=='train/custody'
    market=OfflineMarket(a.train,prefer_csv=True);reports={}
    for delay in (1,2,3):
        fast=ev(nominal,delay);rows=[]
        for i,case in enumerate(actualcases):
            u,o=require_paired_bars(market,case['symbol'],case['contract'],case['trade_date']);session=session_for(case,manifest)
            r=replay_case(case,u,o,session,delay_minutes=delay,strategy_override=strategy);rows.append(r)
            assert r['one_round_trip'] and r['entry']['price']==fast[i,5] and r['exit']['price']==fast[i,6]
            for k,side,field in [(0,'entry','signal_at'),(1,'entry','at'),(2,'exit','decision_at'),(3,'exit','signal_at'),(4,'exit','at')]:assert (datetime.fromisoformat(r[side][field])-session.opens).total_seconds()/60==fast[i,k]
        reports[str(delay)]={'summary':summarize(rows),'cases':rows};print('aggressive service delay',delay,'124 exact matches',flush=True)
    (out/'aggressive_service_reports.json').write_text(json.dumps(reports,indent=2))
    print(json.dumps({'ablations':ablations,'neighbors':diagnostics['neighbor_summary'],'robust_gross':diagnostics['robust']['gross'],'aggressive_gross':diagnostics['aggressive']['gross']}),flush=True)

if __name__=='__main__':main()
