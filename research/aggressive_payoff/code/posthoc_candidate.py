"""Describe the highest all-history perturbation; explicitly NOT validation or reselection."""
import run as rt
import argparse, json
from pathlib import Path
import pandas as pd
from data import load_release, make_cube, write_json
from engine import aggregate_1m, daily_atr, mtm_drawdown
from protocol import case, rule_for, BASE_EXIT
from features import params
from metrics import metric, scenarios, DISCLAIMER
from dataclasses import asdict, replace

p=argparse.ArgumentParser();p.add_argument('--zip',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=1);a=p.parse_args();out=Path(a.out)
pert=pd.read_csv(out/'parameter_perturbations.csv');peak=pert[(pert.period=='all83')&(pert.cost_bps==2)].sort_values(['payoff_ratio','id'],ascending=[False,True]).iloc[0]
variants=json.loads((out/'parameter_perturbation_cases.json').read_text());c=next({k:v for k,v in x.items() if k!='variation'} for x in variants if x['id']==peak.id)
rec=json.loads((out/'recommended_rule.json').read_text())
write_json(out/'posthoc_peak_rule.json',{'disclaimer':DISCLAIMER,'case':c,'resolved_indicator_params':params(c['entry']),'engine_rule':asdict(rule_for(c)),'data_tag':rec['data_tag'],'sha256':rec['sha256'],'status':'POSTHOC: highest all83 net2bps payoff in 28 frozen perturbations; all83 used to identify it; no independent validation','replaces_frozen_recommendation':False})
frames,cal,_,_=load_release(a.zip);step=c['entry']['signal_minutes'];rt.CUBES={s:make_cube(aggregate_1m(frames['klines_1m'],s),cal,s) for s in {1,step}};rt.EXE=rt.CUBES[1];rt.DAY=frames['klines_day'];n=c['entry']['daily_atr_days'];rt.ADS={n:daily_atr(rt.EXE,rt.DAY,n)};rt.BANKS={};periods=json.loads((out/'data_audit.json').read_text())['periods']
e,x=c['entry'],c['exit'];cases={'posthoc_peak':c,'no_confirmations':case({**e,'gates':[],'quorum':0},x),'previous_exit':case(e,BASE_EXIT)}
for g in e['gates']:cases['without_'+g]=case({**e,'gates':[z for z in e['gates'] if z!=g],'quorum':max(0,e['quorum']-1)},x)
for label,delta in [('no_soft_loss',{'soft':0.}),('no_fast_failure',{'fail':0}),('no_trailing',{'trail':100.,'expanded':0.,'late_trail':0.}),('soft_loss_always_active',{'soft_escape':100.})]:cases[label]=case(e,{**x,**delta})
rows=[];fits=[]
for label,cc in cases.items():
    f,info=rt.run_case(cc,record_mtm=True);rt.verify_fills(f,rt.EXE,rule_for(cc))
    if label=='posthoc_peak':
        f.to_csv(out/'trades'/'posthoc_peak.csv',index=False)
        labels=frames['scenario_labels'];labels=labels[labels.grain=='session_5m']
        pd.DataFrame(scenarios(f,labels,periods,'posthoc_peak')).to_csv(out/'posthoc_peak_scenarios.csv',index=False)
        for period in ['fit28','validation28']:
            fits.append({'period':period,**rt.evaluate(f[f.date.isin(periods[period])])})
    for period,ds in periods.items():
        g=f[f.date.isin(ds)];dd,_=mtm_drawdown(g,info['mtm_returns'])
        for bps in ([0,2,5,10,20] if label=='posthoc_peak' else [2]):rows.append({'variant':label,'period':period,'additional_latency':0,**metric(g,bps),**rt.extras(g),'mtm_mdd_pct_at_2bps':dd})
for latency in [1,2]:
    f,_=rt.run_case(c,latency=latency);rt.verify_fills(f,rt.EXE,replace(rule_for(c),latency_minutes=latency))
    for period,ds in periods.items():rows.append({'variant':'posthoc_peak','period':period,'additional_latency':latency,**metric(f[f.date.isin(ds)])})
pd.DataFrame(rows).to_csv(out/'posthoc_peak_diagnostics.csv',index=False)
pd.DataFrame(fits).to_csv(out/'posthoc_peak_development_scores.csv',index=False)
write_json(out/'posthoc_peak_metadata.json',rt.clean({'posthoc_selection':True,'stress_used_to_identify_peak':True,'only_predefined_perturbations_considered':len(variants),'winner_id':c['id'],'variation':peak.variation,'frozen_recommendation_unchanged':rec['case']['id'],'ablation_cases':cases,'additional_parameters_searched':False,'purpose':'Show the more extreme observed result honestly, with its own ablations and execution sensitivity. It is not an independent validation.'}))
print(f'Posthoc-only peak {c["id"]}: complete ablation/cost/delay diagnostics; frozen recommendation unchanged')
