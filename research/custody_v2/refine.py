"""Predeclared second training-only refinement; selection criteria unchanged."""
import copy,json,random,time
from pathlib import Path
import numpy as np
from search import evaluate,stats,config_id,ED,XD,validate_vectors

def main():
    out=Path(__file__).parent/'results'
    old=json.loads((out/'all_results.json').read_text())
    pressure=json.loads((out/'pressure_results.json').read_text())
    leaders=[]
    for c in pressure:
        if (c['profile'],c['e'][0]) not in [(z['profile'],z['e'][0]) for z in leaders]:leaders.append(c)
        if len(leaders)==10:break
    protocol={'scope':'all124 train only','reason':'refine diverse stress-tested leaders around early-failure/trend-capture tradeoff',
      'seed':20260916,'draws':20000,'selection':'identical to PROTOCOL.json; no holdout accessed',
      'leaders':[c['id'] for c in leaders],'entry_mutations':'0..2 active/declared fields',
      'exit_mutations':'1..5 fields','new_exit_values':{'soft':[0,.25,.5,.75,1,1.5,2], 'grace':[0,2,3,5,8,10], 'fail':[0,3,5,8,10,15,20,30], 'activation':[0,1,2,3,4,6], 'trail':[0,.5,1,1.5,2,3,4,6,8]}}
    (out/'REFINEMENT_PROTOCOL_BEFORE_RESULTS.json').write_text(json.dumps(protocol,indent=2))
    xd=copy.deepcopy(XD);xd[1]=[0,.25,.5,.75,1,1.5,2];xd[2]=[0,2,3,5,8,10];xd[4]=[0,3,5,8,10,15,20,30];xd[8]=[0,1,2,3,4,6];xd[9]=[0,.5,1,1.5,2,3,4,6,8]
    rng=random.Random(protocol['seed']);seen={c['id'] for c in old};items=[]
    for i in range(protocol['draws']):
        lead=leaders[i%len(leaders)];c={k:copy.deepcopy(lead[k]) for k in ('profile','e','x')}
        for k in rng.sample(list(ED),rng.randrange(3)):c['e'][k]=rng.choice(ED[k])
        for k in rng.sample(list(xd),rng.randint(1,5)):c['x'][k]=rng.choice(xd[k])
        try:validate_vectors(c['e'],c['x'])
        except ValueError:continue
        uid=config_id(c['profile'],c['e'],c['x'])
        if uid in seen:continue
        seen.add(uid);c.update(id=uid,parent=lead['id'],family='guided_refinement');items.append(c)
    (out/'REFINEMENT_CANDIDATES_BEFORE_RESULTS.json').write_text(json.dumps(items,separators=(',',':')))
    z=np.load(out/'cache.npz');fs,pp,nb,dte,blocks=[z[k] for k in ('features','prices','next_bar','dtes','blocks')]
    rows=[];trades=[];started=time.monotonic()
    for i,c in enumerate(items):
        t=evaluate(fs[c['profile']],pp,nb,dte,np.array(c['e'],float),np.array(c['x'],float),1)
        rows.append({**c,**stats(t,blocks),'stage':'guided'});trades.append(t)
        if (i+1)%3000==0:print('refine',i+1,'/',len(items),'seconds',round(time.monotonic()-started,1),flush=True)
    (out/'refinement_results.json').write_text(json.dumps(rows,separators=(',',':')))
    np.savez_compressed(out/'refinement_trades.npz',trades=np.array(trades))
    eligible=[r for r in old+rows if r['eligible']]
    chosen={r['id']:r for r in sorted(eligible,key=lambda r:(r['dual_payoff'],r['mean'],r['id']),reverse=True)[:80]}
    for r in sorted(eligible,key=lambda r:(r['block_positive'],min(r['blocks']),r['dual_payoff']),reverse=True)[:40]:chosen[r['id']]=r
    for r in sorted(eligible,key=lambda r:r['mean'],reverse=True)[:40]:chosen[r['id']]=r
    # Preserve original robust winner in the second-stage comparison.
    chosen[pressure[0]['id']]=pressure[0]
    (out/'SHORTLIST2_BEFORE_PRESSURE.json').write_text(json.dumps(list(chosen.values()),indent=2))
    ranked=[]
    for c in chosen.values():
        scenarios=[]
        for delay,bps in [(1,25),(2,25),(3,25),(1,50),(1,100)]:
            t=evaluate(fs[c['profile']],pp,nb,dte,np.array(c['e'],float),np.array(c['x'],float),delay)
            scenarios.append(dict(delay=delay,bps=bps,**stats(t,blocks,bps)))
        passing=sum(m['eligible'] for m in scenarios)+c['block_positive']
        ranked.append({**c,'scenarios':scenarios,'conditions_passed':passing,'robust_feasible':passing==8,'worst_dual_payoff':min(m['dual_payoff'] for m in scenarios)})
    ranked.sort(key=lambda r:(r['conditions_passed'],r['worst_dual_payoff'],r['mean'],r['id']),reverse=True)
    (out/'pressure2_results.json').write_text(json.dumps(ranked,indent=2))
    (out/'SELECTED2_BEFORE_SERVICE_REPLAY.json').write_text(json.dumps(ranked[0],indent=2))
    print('refinement_done',len(rows),'total',len(rows)+len(old),'shortlist',len(ranked),'robust',sum(r['robust_feasible'] for r in ranked),flush=True)
    print('selected',json.dumps(ranked[0]),flush=True)

if __name__=='__main__':main()
