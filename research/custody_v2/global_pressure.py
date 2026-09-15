"""Exact robust selection within the full searched set, with a valid upper bound.

The worst-scenario dual payoff cannot exceed the primary-scenario dual payoff.
Thus candidates below the incumbent robust score cannot win; all other feasible
primary candidates are checked, rather than trusting a heuristic shortlist.
"""
import json
from pathlib import Path
import numpy as np
from search import evaluate,stats

def main():
    out=Path(__file__).parent/'results'
    allrows=json.loads((out/'all_results.json').read_text())+json.loads((out/'refinement_results.json').read_text())
    incumbent=json.loads((out/'SELECTED2_BEFORE_SERVICE_REPLAY.json').read_text())
    assert incumbent['robust_feasible']
    rules={'selection':'unchanged; exact maximization over searched feasible candidates',
       'pruning':'primary dual payoff is an upper bound on worst-scenario dual payoff',
       'feasible':'complete and positive USD/return in all 5 scenarios plus positive primary return in all 3 blocks',
       'incumbent_before_results':incumbent['id'],'candidate_count':len(allrows)}
    (out/'GLOBAL_PRESSURE_PROTOCOL_BEFORE_RESULTS.json').write_text(json.dumps(rules,indent=2))
    z=np.load(out/'cache.npz');fs,pp,nb,dte,blocks=[z[k] for k in ('features','prices','next_bar','dtes','blocks')]
    checked=[];candidates=sorted([r for r in allrows if r['eligible'] and r['block_positive']==3],key=lambda r:(r['dual_payoff'],r['mean'],r['id']),reverse=True)
    for c in candidates:
        if c['dual_payoff']<incumbent['worst_dual_payoff']:break
        scenarios=[]
        for delay,bps in [(1,25),(2,25),(3,25),(1,50),(1,100)]:
            tr=evaluate(fs[c['profile']],pp,nb,dte,np.array(c['e'],float),np.array(c['x'],float),delay)
            scenarios.append(dict(delay=delay,bps=bps,**stats(tr,blocks,bps)))
        passing=sum(m['eligible'] for m in scenarios)+c['block_positive']
        r={**c,'scenarios':scenarios,'conditions_passed':passing,'robust_feasible':passing==8,
           'worst_dual_payoff':min(m['dual_payoff'] for m in scenarios)}
        checked.append(r)
        rank=lambda a:(a['worst_dual_payoff'],a['mean'],a['id'])
        if r['robust_feasible'] and rank(r)>rank(incumbent):incumbent=r
        if len(checked)%500==0:print('global pressure checked',len(checked),'incumbent',incumbent['id'],round(incumbent['worst_dual_payoff'],4),flush=True)
    (out/'global_pressure_results.json').write_text(json.dumps(checked,separators=(',',':')))
    (out/'FINAL_SELECTION_BEFORE_CONFIRMATION.json').write_text(json.dumps(incumbent,indent=2))
    proof={'all_candidates':len(allrows),'primary_and_block_feasible':len(candidates),'checked':len(checked),'pruned_by_upper_bound':len(candidates)-len(checked),
           'selected':incumbent['id'],'optimal_within_declared_searched_set':True,'independent_oos':False}
    (out/'SELECTION_COVERAGE.json').write_text(json.dumps(proof,indent=2))
    print(json.dumps(proof),flush=True);print(json.dumps(incumbent),flush=True)

if __name__=='__main__':main()
