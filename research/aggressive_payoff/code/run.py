#!/usr/bin/env python3
"""Reproducible aggressive payoff search over the fixed real83day Release."""
import os,socket
os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['OMP_NUM_THREADS']='1'
_connect=socket.socket.connect

def no_network(sock,address):
    if sock.family in (socket.AF_INET,socket.AF_INET6):raise RuntimeError('offline: network disabled')
    return _connect(sock,address)
socket.socket.connect=no_network

def blocked(*a,**kw):raise RuntimeError('offline: network disabled')
socket.create_connection=blocked;socket.getaddrinfo=blocked
import argparse,json,hashlib,time,math,gc,multiprocessing
from pathlib import Path
from dataclasses import asdict,replace
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from data import load_release,make_cube,write_json
from engine import Rule,JobSpec,replay,aggregate_1m,daily_atr,mtm_drawdown
from features import Features,params
from protocol import PROTOCOL,BASE_EXIT,PROBES,TRIGGERS,case,identity,entry_cases,exit_cases,loss_time_variants,neighbors,rule_for,previous_entry,valid
from metrics import metric,scenarios,paired_bootstrap,DISCLAIMER

CUBES=BANKS=ADS=EXE=FITEXE=VALEXE=DAY=PERIODS=None

def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [clean(v) for v in x]
    if isinstance(x,np.generic):return clean(x.item())
    if isinstance(x,float) and not np.isfinite(x):return None
    return x

def subset(cube,ix):return {k:(v.iloc[ix].reset_index(drop=True) if k=='keys' else v[ix].copy() if isinstance(v,np.ndarray) else v) for k,v in cube.items()}

def bank_for(e):
    key=(e['signal_minutes'],e['daily_atr_days'])
    if key not in BANKS:BANKS[key]=Features(CUBES[key[0]],ADS[key[1]],DAY)
    return BANKS[key]

def run_case(c,execution=None,record_mtm=False,trace=False,job=None,latency=0,cache=True):
    e=c['entry'];rule=replace(rule_for(c),latency_minutes=latency);bank=bank_for(e);feat=bank.for_replay(e,rule,c['exit']['style'],cache=cache)
    f,info=replay(CUBES[e['signal_minutes']],EXE if execution is None else execution,ADS[e['daily_atr_days']],rule,job=job,feature_override=feat,record_mtm=record_mtm,trace=trace)
    f['rule_id']=c['id'];return f,info

def evaluate(f):
    traded=f.traded.to_numpy();r=f.gross_return.to_numpy()-.0002*traded;x=r[traded];w=x[x>0];l=x[x<0];xx=x-.0003;w5=xx[xx>0];l5=xx[xx<0];n=len(x)
    payoff=float(w.mean()/-l.mean()) if len(w) and len(l) else 0.;pay5=float(w5.mean()/-l5.mean()) if len(w5) and len(l5) else 0.
    pf=float(w.sum()/-l.sum()) if len(l) else 0.;avgwin=float(w.mean()*100) if len(w) else 0.
    eq=np.vstack([np.ones(20),np.cumprod(1+r.reshape(-1,20),axis=0)]);dd=float((eq/np.maximum.accumulate(eq,axis=0)-1).min()*100)
    tail=np.sort(x)[:max(1,math.ceil(.05*n))];mean=float(x.mean()*100) if n else None;cvar=float(tail.mean()*100) if n else None
    nd=int(f.loc[f.traded,'date'].nunique());ns=int(f.loc[f.traded,'symbol'].nunique())
    sample=n>=20 and len(w)>=4 and len(l)>=8 and len(w5)>=4 and len(l5)>=8 and nd>=8 and ns>=5
    robust=min(payoff,pay5);score=float(np.log(max(robust,1e-6))+.20*np.log(max(avgwin,1e-6)/.5))
    return dict(trades=n,wins=len(w),losses=len(l),wins5=len(w5),losses5=len(l5),trade_dates=nd,symbols=ns,win_rate_pct=100*len(w)/n if n else None,payoff_ratio=payoff,payoff5=pay5,robust_payoff=robust,avg_win_pct=avgwin,avg_loss_pct=float(l.mean()*100) if len(l) else None,profit_factor=pf,mean_trade_pct=mean,mean_job_bps=float(r.mean()*10000),worst_stream_mdd_pct=dd,cvar05_pct=cvar,sample_ok=sample,score=score)

def fit_task(c):
    f,_=run_case(c,FITEXE);return {'id':c['id'],**evaluate(f)}

def val_task(c):
    f,_=run_case(c,VALEXE);return {'id':c['id'],**evaluate(f)}

def rank(m):return (m['sample_ok'],m['score'],m['id'])

def run_pool(configs,fn,out,stage,workers,start):
    rows=[]
    with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('fork')) as pool:
        for i,row in enumerate(pool.map(fn,configs,chunksize=12)):
            rows.append(row)
            if (i+1)%1000==0 or i+1==len(configs):print(f'{stage}: {i+1}/{len(configs)}, elapsed {time.time()-start:.1f}s',flush=True)
    pd.DataFrame(rows).to_csv(out/(stage+'.csv'),index=False);return {r['id']:r for r in rows}

def warm(configs):
    seen=set()
    for c in configs:
        eid=identity(c['entry'])
        if eid not in seen:bank_for(c['entry']).entry(c['entry']);seen.add(eid)
        rule=rule_for(c);bank_for(c['entry']).for_replay(c['entry'],rule,c['exit']['style'])

def top_diverse(mm,cc,per_step,per_entry=2):
    out=[]
    for step in [1,5,15]:
        counts={}
        for r in sorted([r for r in mm.values() if cc[r['id']]['entry']['signal_minutes']==step],key=rank,reverse=True):
            eid=identity(cc[r['id']]['entry'])
            if counts.get(eid,0)>=per_entry:continue
            out.append(cc[r['id']]);counts[eid]=counts.get(eid,0)+1
            if sum(counts.values())>=per_step:break
    return out

def extras(f):
    t=f[f.traded];r=t.gross_return.to_numpy()-.0002;w=np.sort(r[r>0]);n=len(r);profits=w.sum()
    return dict(trading_dates=t.date.nunique(),symbols_traded=t.symbol.nunique(),max_win_pct=float(w[-1]*100) if len(w) else None,p90_win_pct=float(np.quantile(w,.9)*100) if len(w) else None,top1_winners_share_pct=float(w[-1:].sum()/profits*100) if profits else None,top5_winners_share_pct=float(w[-5:].sum()/profits*100) if profits else None,mean_without_best3_pct=float(np.sort(r)[:-3].mean()*100) if n>3 else None,mean_hold_minutes=float((t.exit_minute-t.entry_minute).mean()) if n else None)

def verify_fills(f,exe,rule):
    ix={(r.date,r.symbol):i for i,r in exe['keys'].iterrows()};assert not f.duplicated(['date','symbol','direction']).any()
    for r in f[f.traded].itertuples():
        i=ix[(r.date,r.symbol)];assert r.entry_price==exe['open'][i,r.entry_index];assert r.entry_minute>=r.entry_signal_close_minute+rule.latency_minutes
        assert (r.entry_signal_close_minute-570)%rule.signal_minutes==0;assert rule.entry_start<=r.entry_signal_close_minute<=rule.entry_deadline
        assert r.exit_index>=r.entry_index and r.exit_minute<=min(exe['keys'].iloc[i].flatten,rule.flatten_minute)
        if r.exit_reason=='hard_stop_intrabar':assert exe['low'][i,r.exit_index]<=r.exit_price<=exe['high'][i,r.exit_index]
        else:assert r.exit_price==exe['open'][i,r.exit_index]
        if r.exit_reason in ['trailing','structural','failed_followthrough','indicator_reversal','soft_failure_loss','winner_stall']:
            assert r.exit_minute>=r.exit_signal_close_minute+rule.latency_minutes and (r.exit_signal_close_minute-570)%rule.signal_minutes==0
        assert not r.unclosed
    return int(f.traded.sum())

def main():
    global CUBES,BANKS,ADS,EXE,FITEXE,VALEXE,DAY,PERIODS
    p=argparse.ArgumentParser();p.add_argument('--zip',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=6);a=p.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True);start=time.time()
    entries=entry_cases();stagea=[case(e,x) for e in entries for x in PROBES];write_json(out/'protocol_before_results.json',clean({**PROTOCOL,'stageA_entry_specs':len(entries),'stageA_evaluations':len(stagea)}));write_json(out/'stageA_configs.json',stagea)
    hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in Path(__file__).parent.glob('*.py')};write_json(out/'source_checksums_at_start.json',hashes)
    print('Verify pinned real Release ZIP SHA256, manifest, internal checksums, exact calendar',flush=True)
    frames,cal,dataaudit,_=load_release(a.zip);raw=frames['klines_1m'];DAY=frames['klines_day'];dates=sorted(raw.date.unique());assert len(dates)==83
    PERIODS={'fit28':dates[:28],'validation28':dates[28:56],'development56':dates[:56],'stress27':dates[56:],'all83':dates};write_json(out/'data_audit.json',{**dataaudit,'periods':PERIODS})
    CUBES={s:make_cube(aggregate_1m(raw,s),cal,s) for s in [1,5,15]};EXE=CUBES[1];ADS={n:daily_atr(EXE,DAY,n) for n in [10,20,40]};BANKS={}
    FITEXE=subset(EXE,np.flatnonzero(EXE['keys'].date.isin(dates[:28])));VALEXE=subset(EXE,np.flatnonzero(EXE['keys'].date.isin(dates[28:56])))
    from audit import prior_parity
    write_json(out/'prior_compatibility_before_search.json',prior_parity(CUBES,EXE,ADS[20],DAY,case(previous_entry(),BASE_EXIT)))
    print(f'Precompute causal entry masks for {len(entries)} entry specs',flush=True)
    for step in [1,5,15]:
        warm([c for c in stagea if c['entry']['signal_minutes']==step]);print(f'{step}m features ready; elapsed {time.time()-start:.1f}s',flush=True)
    ma=run_pool(stagea,fit_task,out,'stageA_fit',a.workers,start);ca={c['id']:c for c in stagea};retained=[]
    for step in [1,5,15]:
        for trigger in TRIGGERS:
            rr=sorted([r for r in ma.values() if ca[r['id']]['entry']['signal_minutes']==step and ca[r['id']]['entry']['trigger']==trigger],key=rank,reverse=True)
            best=ca[rr[0]['id']]['entry'];retained.append(best);other=[r for r in rr if ca[r['id']]['entry']!=best];multi=[r for r in other if len(ca[r['id']]['entry']['gates'])>=2];retained.append(ca[(multi or other)[0]['id']]['entry'])
    retained=list({identity(e):e for e in [*retained,previous_entry()]}.values());write_json(out/'retained_entries_before_exits.json',retained)
    for bank in BANKS.values():bank.entries.clear()
    gc.collect()
    stageb=list({c['id']:c for e in retained for x in [*exit_cases(),*PROBES] for c in [case(e,x)]}.values());write_json(out/'stageB_configs.json',stageb);warm(stageb)
    mb=run_pool(stageb,fit_task,out,'stageB_fit',a.workers,start);cb={c['id']:c for c in stageb};losseeds=[]
    for e in retained:
        rr=sorted([r for r in mb.values() if cb[r['id']]['entry']==e],key=rank,reverse=True);losseeds.extend(cb[r['id']] for r in rr[:2])
    stagec=list({c['id']:c for seed in losseeds for c in loss_time_variants(seed) if valid(c)}.values());write_json(out/'stageC_configs.json',stagec);warm(stagec)
    mc=run_pool(stagec,fit_task,out,'stageC_fit',a.workers,start);cc={c['id']:c for c in stagec};allfit={**mb,**mc};allcases={**cb,**cc}
    local_seeds=top_diverse(allfit,allcases,8);write_json(out/'local_seeds_before_refinement.json',local_seeds)
    staged=list({c['id']:c for seed in local_seeds for c in neighbors(seed)}.values());write_json(out/'stageD_configs.json',staged);warm(staged)
    md=run_pool(staged,fit_task,out,'stageD_fit',a.workers,start);cd={c['id']:c for c in staged};allfit.update(md);allcases.update(cd)
    short=top_diverse(allfit,allcases,20);prev=case(previous_entry(),BASE_EXIT)
    if prev['id'] not in {c['id'] for c in short}:short.append(prev)
    if prev['id'] not in allfit:allfit[prev['id']]=fit_task(prev);allcases[prev['id']]=prev
    write_json(out/'shortlist_before_validation.json',short);vm=run_pool(short,val_task,out,'validation_shortlist',a.workers,start)
    selections=[]
    for c in short:
        f,v=allfit[c['id']],vm[c['id']];stability=abs(np.log(max(f['robust_payoff'],1e-6)/max(v['robust_payoff'],1e-6)))
        score=min(f['score'],v['score'])+.25*(f['score']+v['score'])/2-.1*stability
        selections.append(dict(id=c['id'],signal_minutes=c['entry']['signal_minutes'],sample_ok=f['sample_ok'] and v['sample_ok'],selection_score=score,entry=c['entry'],exit=c['exit'],fit=f,validation=v))
    selections.sort(key=lambda r:(r['sample_ok'],r['selection_score'],r['id']),reverse=True);winners=[next(r for r in selections if r['signal_minutes']==s) for s in [1,5,15]];best=selections[0];overall=allcases[best['id']]
    write_json(out/'LOCKED_SELECTION_BEFORE_STRESS.json',clean({'overall':best,'per_timeframe':winners,'ranked_shortlist':selections,'stress_used_for_selection':False,'winrate_pf_expectancy_gates':False}))
    print('Frozen selection: '+json.dumps({str(r['signal_minutes']):r['id'] for r in winners})+'; overall '+best['id'],flush=True)
    reported={f'selected_{r["signal_minutes"]}m':allcases[r['id']] for r in winners};reported['previous_payoff_4_45']=prev;e,x=overall['entry'],overall['exit']
    reported['ablation_no_confirmations']=case({**e,'gates':[],'quorum':0},x)
    for g in e['gates']:reported['ablation_without_'+g]=case({**e,'gates':[a for a in e['gates'] if a!=g],'quorum':max(0,e['quorum']-1)},x)
    reported['ablation_previous_entry']=case(previous_entry(e['signal_minutes']),x)
    reported['ablation_previous_exit']=case(e,BASE_EXIT)
    for name,delta in [('no_soft_loss',dict(soft=0.)),('no_fast_failure',dict(fail=0)),('no_structure',dict(style='none',structure_minutes=0)),('no_trailing',dict(trail=100.,expanded=0.,late_trail=0.)),('no_expanded_trail',dict(expanded=0.)),('no_stall',dict(stall=0)),('no_late_tightening',dict(late_trail=0.)),('flatten1545',dict(flatten=945)),('safety_08',dict(safety=.8)),('soft_loss_always_active',dict(soft_escape=100.))]:reported['ablation_'+name]=case(e,{**x,**delta})
    reported['ablation_entry_after1045']=case({**e,'entry_start_override':max(645,570+e['opening_minutes'])},x)
    write_json(out/'reported_configs.json',reported)
    report_frames={};summary=[];cost=[];scene=[];bysym=[];reasons=[];fillchecks={};tradeout=out/'trades';tradeout.mkdir(exist_ok=True);labels=frames['scenario_labels'];labels=labels[labels.grain=='session_5m']
    for name,c in reported.items():
        f,info=run_case(c,record_mtm=True);report_frames[name]=f;f.to_csv(tradeout/(name+'.csv'),index=False);fillchecks[name]=verify_fills(f,EXE,rule_for(c))
        for period,ds in PERIODS.items():
            g=f[f.date.isin(ds)];dd,_=mtm_drawdown(g,info['mtm_returns']);summary.append({'name':name,'id':c['id'],'signal_minutes':c['entry']['signal_minutes'],'period':period,**metric(g),**extras(g),'mtm_mdd_pct':dd})
            for bps in [0,2,5,10,20]:cost.append({'name':name,'period':period,**metric(g,bps)})
            for why,t in g[g.traded].groupby('exit_reason'):reasons.append({'name':name,'period':period,'reason':why,'trades':len(t),'mean_net_pct':float((t.gross_return-.0002).mean()*100)})
        if name.startswith('selected_') or name=='previous_payoff_4_45':
            scene.extend(scenarios(f,labels,PERIODS,name))
            for period,ds in PERIODS.items():
                z=f[f.date.isin(ds)]
                for (sym,d),g in z.groupby(['symbol','direction']):bysym.append({'name':name,'period':period,'symbol':sym,'direction':d,**metric(g)})
                for d,g in z.groupby('direction'):bysym.append({'name':name,'period':period,'symbol':'ALL','direction':d,**metric(g)})
                for sym in sorted(z.symbol.unique()):bysym.append({'name':name,'period':period,'symbol':'EXCLUDE_'+sym,'direction':'BOTH_COUNTERFACTUAL',**metric(z[z.symbol!=sym])})
    pd.DataFrame(summary).to_csv(out/'COMPARISON.csv',index=False);pd.DataFrame(cost).to_csv(out/'cost_sensitivity.csv',index=False);pd.DataFrame(scene).to_csv(out/'scenarios.csv',index=False);pd.DataFrame(bysym).to_csv(out/'by_symbol_direction.csv',index=False);pd.DataFrame(reasons).to_csv(out/'exit_reasons.csv',index=False)
    lat=[]
    for r in winners:
        for mins in [1,2]:
            c=allcases[r['id']];f,_=run_case(c,latency=mins);verify_fills(f,EXE,replace(rule_for(c),latency_minutes=mins))
            for period,ds in PERIODS.items():lat.append({'name':f'selected_{r["signal_minutes"]}m','period':period,'latency_minutes':mins,**metric(f[f.date.isin(ds)])})
    pd.DataFrame(lat).to_csv(out/'latency_sensitivity.csv',index=False)
    from audit import run_audits
    tests=run_audits(raw,DAY,cal,reported,report_frames);write_json(out/'audit_tests.json',clean({**tests,'fill_checks':fillchecks}))
    trials=dict(stageA=len(stagea),stageB=len(stageb),stageC=len(stagec),stageD=len(staged));meta={'elapsed_seconds':time.time()-start,'trials':trials,'unique_fit_configs':len(set(ca)|set(allcases)),'validation_candidates':len(short),'sample_adequate_validation_candidates':sum(r['sample_ok'] for r in selections),'best_id':best['id'],'best_signal_minutes':best['signal_minutes'],'best_both_blocks_sample_ok':best['sample_ok'],'selection_objective':'payoff_only_with_average_winner_size','winrate_pf_expectancy_gates':False,'data_tag':dataaudit['tag'],'sha256':dataaudit['sha256'],'network_disabled':True,'true_option_pnl':False}
    write_json(out/'run_metadata.json',clean(meta));write_json(out/'recommended_rule.json',clean({'disclaimer':DISCLAIMER,'case':overall,'resolved_indicator_params':params(overall['entry']),'engine_rule':asdict(rule_for(overall)),'selection':best,'data_tag':dataaudit['tag'],'sha256':dataaudit['sha256'],'gate_for_future_work':None}))
    from diagnostics import build as diagnostics
    diagnostics(out)
    from report import build
    build(out)
    print(pd.DataFrame(summary).query("period=='all83' and (name.str.startswith('selected_') or name=='previous_payoff_4_45')",engine='python')[['name','trades','win_rate_pct','avg_win_pct','avg_loss_pct','payoff_ratio','mean_trade_pct']].to_string(index=False),flush=True);print(json.dumps(meta),flush=True)

if __name__=='__main__':main()
