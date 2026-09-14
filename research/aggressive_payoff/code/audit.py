"""Causal and execution checks against actual pinned Release bars."""
from dataclasses import replace
from pathlib import Path
import json
import numpy as np
import pandas as pd
from data import make_cube
from engine import Rule,JobSpec,replay,aggregate_1m,daily_atr
from previous_engine import Rule as PreviousRule,replay as previous_replay
from previous_features import Features as PreviousFeatures
from features import Features,PROFILE_PARAMS,FAMILY_KEYS
from protocol import rule_for

def subset(cube,ix):return {k:(v.iloc[ix].reset_index(drop=True) if k=='keys' else v[ix].copy() if isinstance(v,np.ndarray) else v) for k,v in cube.items()}

def prior_parity(cubes,exe,ad,day,c):
    record=json.loads((Path(__file__).parent/'previous_recommended_rule.json').read_text());oldcase=record['case'];oldrule=PreviousRule(**record['engine_rule'])
    oldbank=PreviousFeatures(cubes[1],ad,day);of=oldbank.for_replay(oldcase['entry'],oldrule,oldcase['exit']['style']);old,_=previous_replay(cubes[1],exe,ad,oldrule,feature_override=of)
    newbank=Features(cubes[1],ad,day);newrule=rule_for(c);nf=newbank.for_replay(c['entry'],newrule,c['exit']['style']);new,_=replay(cubes[1],exe,ad,newrule,feature_override=nf)
    pd.testing.assert_frame_equal(old.drop(columns='rule_id'),new.drop(columns='rule_id'),check_exact=True)
    assert int(new.traded.sum())==103
    return {'previous_payoff_4_45_exact_trade_parity':True,'jobs':len(new),'trades':int(new.traded.sum()),'default_extensions_off_compatible':True}

def run_audits(raw,day,cal,reported,report_frames):
    cubes={s:make_cube(aggregate_1m(raw,s),cal,s) for s in [1,5,15]};exe=cubes[1];ads={n:daily_atr(exe,day,n) for n in [10,20,40]};banks={};dates=sorted(raw.date.unique());out={'all_passed':False,'future_poison':[],'one_roundtrip_per_human_job':True}
    def getbank(step,n):
        if (step,n) not in banks:banks[(step,n)]=Features(cubes[step],ads[n],day)
        return banks[(step,n)]
    for di,cut in [(27,597),(60,692)]:
        date=dates[di];changed=raw.copy();mask=(changed.date>date)|((changed.date==date)&(changed.minute_close>cut));changed.loc[mask,['open','high','low','close']]*=1.37;changed.loc[mask,'volume']*=7
        dday=day.copy();dday.loc[dday.date>=date,['open','high','low','close']]*=1.9
        pc={s:make_cube(aggregate_1m(changed,s),cal,s) for s in [1,5,15]};pa={n:daily_atr(pc[1],dday,n) for n in [10,20,40]};ix=exe['keys'].index[exe['keys'].date==date].to_numpy()
        for n in ads:np.testing.assert_array_equal(ads[n][ix],pa[n][ix])
        for step in [1,5,15]:
            end=(cut-570)//step;original=getbank(step,20);poison=Features(pc[step],pa[20],dday)
            for p in PROFILE_PARAMS:
                for family in FAMILY_KEYS:np.testing.assert_array_equal(original.gate(family,p)[ix,:,:end],poison.gate(family,p)[ix,:,:end],err_msg=family)
            c=reported[f'selected_{step}m'];e=c['entry'];rule=rule_for(c);n=e['daily_atr_days'];b=getbank(step,n);pb=poison if n==20 else Features(pc[step],pa[n],dday)
            fa=b.for_replay(e,rule,c['exit']['style']);fb=pb.for_replay(e,rule,c['exit']['style'])
            for name in fa:
                aa,bb=fa[name],fb[name]
                if aa.ndim==1:aa,bb=aa[ix],bb[ix]
                elif aa.ndim==2:aa,bb=aa[ix,:end],bb[ix,:end]
                else:aa,bb=aa[ix,:,:end],bb[ix,:,:end]
                np.testing.assert_allclose(aa,bb,atol=0,rtol=0,equal_nan=True,err_msg=name)
            _,a=replay(cubes[step],subset(exe,ix),ads[n],rule,feature_override=fa,trace=True);_,z=replay(pc[step],subset(pc[1],ix),pa[n],rule,feature_override=fb,trace=True)
            assert [q for q in a['trace'] if q['minute_close']<=cut]==[q for q in z['trace'] if q['minute_close']<=cut]
        out['future_poison'].append({'date':date,'cut_close_minute':cut,'all39_indicator_profile_gates':True,'all3_selected_native_state_paths':True,'daily_ATR_lookbacks':[10,20,40]})
    count=0;checks={'soft_failure_loss':0,'failed_followthrough':0}
    for step in [1,5,15]:
        c=reported[f'selected_{step}m'];e=c['entry'];rule=rule_for(c);ad=ads[e['daily_atr_days']];bank=getbank(step,e['daily_atr_days']);feat=bank.for_replay(e,rule,c['exit']['style']);full=report_frames[f'selected_{step}m']
        for sym,d in [('SPY','LONG'),('NVDA','SHORT')]:
            job=JobSpec(sym,d,rule=rule);f,_=replay(cubes[step],exe,ad,rule,feature_override=feat,job=job);f.rule_id=c['id'];pd.testing.assert_frame_equal(f,full[(full.symbol==job.symbol)&(full.direction==d)].reset_index(drop=True));count+=1
        idx={(r.date,r.symbol):i for i,r in exe['keys'].iterrows()}
        for t in full[full.exit_reason.isin(checks)].itertuples():
            k=idx[(t.date,t.symbol)];sign=1 if t.direction=='LONG' else -1;end=t.exit_signal_close_minute;j=(end-570)//step-1;start=t.entry_index//step
            prices=cubes[step]['close'][k,start:j+1];gain=max(0.,float(np.max(sign*(prices-t.entry_price))));current=sign*(prices[-1]-t.entry_price);elapsed=end-t.entry_minute
            if t.exit_reason=='soft_failure_loss':assert elapsed>=rule.soft_stop_grace_minutes and gain<rule.soft_stop_until_best_gain_daily_atr*ad[k] and current<=-rule.soft_stop_daily_atr*ad[k]+1e-12
            else:assert elapsed>=rule.fail_after_minutes and gain<rule.fail_required_progress_daily_atr*ad[k] and current<=0
            checks[t.exit_reason]+=1
    out['single_job_batch_parity']=count;out['close_based_failure_checks']=checks
    c=reported['previous_payoff_4_45'];e=c['entry'];ad=ads[20];rule=replace(rule_for(c),structure_minutes=0,trailing_daily_atr=100,expanded_trailing_daily_atr=0,hard_stop_daily_atr=100,fail_after_minutes=0,max_hold_minutes=390,flatten_minute=945)
    feat=getbank(1,20).for_replay(e,rule,'none');f,_=replay(cubes[1],exe,ad,rule,feature_override=feat);chosen=f[f.traded].iloc[0];ix=exe['keys'].index[(exe['keys'].date==chosen.date)&(exe['keys'].symbol==chosen.symbol)].to_numpy();job=JobSpec(chosen.symbol,chosen.direction,rule=rule);bad=subset(exe,ix)
    for key in ['open','high','low','close']:bad[key][:,375:]=np.nan
    try:replay(cubes[1],bad,ad,rule,feature_override=feat,job=job)
    except ValueError as exc:assert 'unclosed' in str(exc)
    else:raise AssertionError('missing flatten silently accepted')
    early=subset(exe,ix);early['keys'].loc[:,'flatten']=765;f,_=replay(cubes[1],early,ad,rule,feature_override=feat,job=job);assert f.traded.all() and f.exit_minute.eq(765).all()
    out['missing_flatten_fails']=True;out['known_early_flatten_passed']=True
    try:JobSpec('SPY','LONG',dry_run=False)
    except ValueError:pass
    else:raise AssertionError('live job accepted')
    out['all_passed']=True;return out
