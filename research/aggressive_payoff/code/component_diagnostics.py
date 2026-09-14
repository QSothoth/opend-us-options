"""Clarify quorum ablations and actual entry votes; no parameter selection."""
import run as rt
import argparse,json,itertools
from pathlib import Path
import numpy as np
import pandas as pd
from data import load_release,make_cube,write_json
from engine import aggregate_1m,daily_atr
from features import params
from protocol import case,rule_for
from metrics import metric

p=argparse.ArgumentParser();p.add_argument('--zip',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=6);a=p.parse_args();out=Path(a.out);rec=json.loads((out/'recommended_rule.json').read_text());base=rec['case'];e,x=base['entry'],base['exit'];step=e['signal_minutes']
frames,cal,_,_=load_release(a.zip);rt.CUBES={s:make_cube(aggregate_1m(frames['klines_1m'],s),cal,s) for s in {1,step}};rt.EXE=rt.CUBES[1];rt.DAY=frames['klines_day'];rt.ADS={e['daily_atr_days']:daily_atr(rt.EXE,rt.DAY,e['daily_atr_days'])};rt.BANKS={};periods=json.loads((out/'data_audit.json').read_text())['periods']
cases={'selected':base}
for g in e['gates']:
    remaining=[z for z in e['gates'] if z!=g]
    cases['drop_vote_keep_quorum_'+g]=case({**e,'gates':remaining,'quorum':min(e['quorum'],len(remaining))},x)
    cases['only_'+g]=case({**e,'gates':[g],'quorum':1},x)
if len(e['gates'])==3:
    cases['all_three_AND']=case({**e,'quorum':3},x)
    cases['any_one_of_three']=case({**e,'quorum':1},x)
rows=[];cached={}
for label,c in cases.items():
    if c['id'] not in cached:cached[c['id']]=rt.run_case(c)[0]
    f=cached[c['id']]
    for period,ds in periods.items():
        for cost in [2,5]:rows.append({'variant':label,'id':c['id'],'period':period,**metric(f[f.date.isin(ds)],cost)})
pd.DataFrame(rows).to_csv(out/'quorum_and_single_indicator_ablations.csv',index=False)
selected=cached[base['id']];bank=rt.bank_for(e);pp=params(e);ix={(r.date,r.symbol):i for i,r in rt.EXE['keys'].iterrows()};votes=[]
for r in selected[selected.traded].itertuples():
    i=ix[(r.date,r.symbol)];di=0 if r.direction=='LONG' else 1;j=(r.entry_signal_close_minute-570)//step-1
    v={g:bool(bank.gate(g,pp)[i,di,j]) for g in e['gates']};count=sum(v.values());votes.append({'date':r.date,'symbol':r.symbol,'direction':r.direction,'entry_signal_close_minute':r.entry_signal_close_minute,'votes':count,'required_votes':e['quorum'],**v,**{g+'_decisive_at_entry':bool(ok and count==e['quorum']) for g,ok in v.items()}})
vf=pd.DataFrame(votes);vf.to_csv(out/'entry_indicator_votes.csv',index=False);summ=[]
for g in e['gates']:
    for period,ds in periods.items():
        v=vf[vf.date.isin(ds)] if len(vf) else vf
        summ.append({'indicator':g,'period':period,'entries':len(v),'true_at_entry_pct':float(v[g].mean()*100) if len(v) else None,'decisive_at_entry_pct':float(v[g+'_decisive_at_entry'].mean()*100) if len(v) else None})
pd.DataFrame(summ).to_csv(out/'indicator_vote_summary.csv',index=False)
write_json(out/'component_diagnostics_metadata.json',{'no_reranking':True,'selected_id_unchanged':base['id'],'variant_configs':cases,'definitions':{'main_ablation':'Replace removed condition by true and reduce required votes by1: relaxation of a constraint.','drop_vote_keep_quorum':'Remove the indicator vote, keep required votes capped at remaining indicator count: deletion of information. May be stricter for2-of3.','entry_decisiveness':'True indicator with totalvotes==quorum at actual entry; descriptive, does not by itself establish causal value or account for a different earlier entry.'},'motivation':'Training candidates include2-of3 quorum rules. Both ablation meanings are reported to avoid attributing quota changes to an individual indicator.'})
print(f'Quorum/single-indicator diagnostics: {len(cases)} named variants, selection unchanged')
