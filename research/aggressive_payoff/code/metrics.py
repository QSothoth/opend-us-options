"""Descriptive underlying return proxies; no option price or leverage model."""
import math
import numpy as np
import pandas as pd
from data import SCENARIOS
DISCLAIMER='underlying proxy, not true option PnL'

def metric(f,cost=2):
    t=f[f.traded];r=t.gross_return.to_numpy()-cost/10000
    w=r[r>0];l=r[r<0];n=len(r)
    dd=0.
    for _,g in f.groupby(['symbol','direction'],sort=False):
        g=g.sort_values('date');net=g.gross_return.to_numpy()-g.traded.to_numpy()*cost/10000
        e=np.r_[1.,np.cumprod(1+net)];dd=min(dd,float((e/np.maximum.accumulate(e)-1).min()))
    tail=np.sort(r)[:max(1,math.ceil(.05*n))]
    trim=np.sort(r)[:n-max(1,math.ceil(.01*n))]
    return {'jobs':len(f),'trades':n,'trade_rate_pct':100*n/len(f) if len(f) else 0.,'cost_bps':cost,
            'win_rate_pct':100*len(w)/n if n else None,
            'avg_win_pct':100*float(w.mean()) if len(w) else None,'avg_loss_pct':100*float(l.mean()) if len(l) else None,
            'payoff_ratio':float(w.mean()/-l.mean()) if len(w) and len(l) else None,
            'profit_factor':float(w.sum()/-l.sum()) if len(l) else None,
            'mean_trade_pct':100*float(r.mean()) if n else None,
            'mean_job_bps':10000*float(r.sum())/len(f) if len(f) else 0,
            'mean_gross_points':float(t.pnl_points.mean()) if n else None,
            'worst_stream_mdd_pct':100*dd,
            'p05_pct':100*float(np.quantile(r,.05)) if n else None,
            'cvar05_pct':100*float(tail.mean()) if n else None,
            'worst_trade_pct':100*float(r.min()) if n else None,
            'trim_top1_mean_pct':100*float(trim.mean()) if len(trim) else None,
            'held_until_flatten_pct':100*float(t.eod_flatten.mean()) if n else None,
            'unclosed_after_day_pct':100*float(t.unclosed.mean()) if n else 0}

def assess(f,dates):
    f=f[f.date.isin(dates)];m=metric(f)
    blocks=np.array_split(dates,3)
    block_m=[metric(f[f.date.isin(d)]) for d in blocks]
    worst=min(x['mean_job_bps'] for x in block_m)
    minimum=max(60,int(len(dates)*.8))
    sample=m['trades']>=minimum and all(x['trades']>=max(15,minimum//4) for x in block_m)
    risk=m['worst_stream_mdd_pct']>=-12 and (m['cvar05_pct'] or -999)>=-1.8
    stable=worst>=0 and (m['trim_top1_mean_pct'] or -999)>=0
    payoff=(m['payoff_ratio'] or 0)>=1.2
    eligible=sample and risk and stable and payoff and (m['profit_factor'] or 0)>=1.1
    score=m['mean_job_bps']+worst-.10*abs(m['worst_stream_mdd_pct'])-.05*abs(m['cvar05_pct'] or 0)
    return {**m,'eligible':eligible,'sample_ok':sample,'risk_ok':risk,'stable_blocks':stable,'payoff_ge_1_2':payoff,'worst_block_job_bps':worst,'score':score}

def ranking_key(m,rid=''):
    return (m['eligible'],m['sample_ok'],m['risk_ok'],m['score'],m['mean_job_bps'],rid)

def scenarios(f,labels,periods,rule):
    j=f.merge(labels[['symbol','date',*SCENARIOS]],on=['symbol','date'],validate='many_to_one')
    assert len(j)==len(f)
    rows=[]
    for p,dates in periods.items():
        s=j[j.date.isin(dates)]
        for direction in ['BOTH_COUNTERFACTUAL','LONG','SHORT']:
            g=s if direction=='BOTH_COUNTERFACTUAL' else s[s.direction==direction]
            for name in ['ALL',*SCENARIOS]:
                rows.append({'rule':rule,'period':p,'direction':direction,'scenario':name,**metric(g if name=='ALL' else g[g[name]])})
    return rows

def paired_bootstrap(a,b,draws=3000):
    f=a.merge(b,on=['date','symbol','direction'],suffixes=('_a','_b'),validate='one_to_one')
    assert len(f)==len(a)==len(b)
    d=f.assign(delta=(f.gross_return_a-.0002*f.traded_a)-(f.gross_return_b-.0002*f.traded_b)).groupby('date').delta.mean().to_numpy()*10000
    rng=np.random.default_rng(20260914);starts=rng.integers(0,len(d),size=(draws,math.ceil(len(d)/5)))
    ix=((starts[:,:,None]+np.arange(5))%len(d)).reshape(draws,-1)[:,:len(d)]
    boot=d[ix].mean(axis=1)
    return {'mean_delta_bps_per_job':float(d.mean()),'ci95_low':float(np.quantile(boot,.025)),'ci95_high':float(np.quantile(boot,.975)),'block_days':5,'draws':draws,'seed':20260914,'interpretation':'paired date-cluster descriptive CI on reused history; not multiple-selection-adjusted'}
