"""Option-only economics and bounded, date/scenario-balanced timing scores."""
import numpy as np

LABELS=('trend_favourable','trend_adverse','reversal_up','reversal_down','chop')


def label_path(bars, sign):
    p0=sign*bars[0].open
    close=sign*np.array([b.close for b in bars])
    hi=np.array([sign*(b.high if sign==1 else b.low) for b in bars])
    lo=np.array([sign*(b.low if sign==1 else b.high) for b in bars])
    high,low=float(hi.max()),float(lo.min());ran=high-low
    if ran<=0:return 4
    delta=close[-1]-p0;length=np.abs(np.diff(np.r_[p0,close])).sum()
    efficiency=abs(delta)/length if length>0 else 0.
    if delta>=.65*ran and p0-low<=.20*ran and efficiency>=.08:return 0
    if delta<=-.65*ran and high-p0<=.20*ran and efficiency>=.08:return 1
    imin,imax=int(lo.argmin())+1,int(hi.argmax())+1
    up=15<=imin<=360 and p0-low>=.25*ran and close[-1]-low>=.50*ran
    down=15<=imax<=360 and high-p0>=.25*ran and high-close[-1]>=.50*ran
    if up and (not down or close[-1]-low>=high-close[-1]):return 2
    if down:return 3
    return 4


def economics(trades,bps=25):
    complete=(trades[...,0]>=0)&(trades[...,4]>=0)
    a=trades[...,5]*(1+bps/10000);b=trades[...,6]*(1-bps/10000)
    dollars=np.where(complete,100*(b-a)-1.30,np.nan)
    returns=np.where(complete,dollars/(100*np.maximum(a,1e-100)),np.nan)
    return dollars,returns


def percentile(returns,controls):
    valid=np.isfinite(controls);n=valid.sum(axis=1)
    below=((controls<returns[:,None]-1e-12)&valid).sum(axis=1)
    equal=((np.abs(controls-returns[:,None])<=1e-12)&valid).sum(axis=1)
    return np.where((n>0)&np.isfinite(returns),100*(below+.5*equal)/np.maximum(n,1),np.nan)


def group_mean(values,dates,labels,mask):
    out={}
    for k,name in enumerate(LABELS):
        m=mask&(labels==k)
        if not np.any(m):continue
        per_date=[float(np.mean(values[m&(dates==d)])) for d in np.unique(dates[m])]
        out[name]={'value':float(np.mean(per_date)),'cases':int(m.sum()),'dates':len(per_date)}
    return out


def describe(trades,controls,dates,labels,mask=None,bps=25):
    if mask is None:mask=np.ones(len(dates),dtype=bool)
    dollars,returns=economics(trades,bps);score=percentile(returns,controls)
    complete=np.isfinite(returns)&mask
    def ratio(v):
        w,l=v[v>1e-10],v[v<-1e-10]
        return float(w.mean()/(-l.mean())) if len(w) and len(l) else 0.
    d,r=dollars[complete],returns[complete]
    groups=group_mean(score,dates,labels,mask)
    raw=group_mean(returns,dates,labels,mask)
    non=[v['value'] for k,v in groups.items() if k in LABELS[2:]]
    full=bool(complete.sum()==mask.sum())
    return dict(count=int(mask.sum()),completed=int(complete.sum()),pnl=float(d.sum()) if full else None,
                mean_return=float(r.mean()) if full else None,median_return=float(np.median(r)) if len(r) else None,
                payoff_dollar=ratio(d),payoff_return=ratio(r),dual_payoff=min(ratio(d),ratio(r)),
                win_rate=float((d>0).mean()) if len(d) else 0.,
                profit_factor=float(d[d>0].sum()/abs(d[d<0].sum())) if np.any(d<0) else None,
                macro_score=float(np.mean([v['value'] for v in groups.values()])),
                nontrend_score=float(np.mean(non)) if non else None,groups=groups,group_returns=raw,
                holding_minutes=float(np.mean((trades[:,4]-trades[:,1])[complete])) if len(d) else None)


def feasible(metrics, reference):
    p=metrics[0];r=reference[0]
    economic=all(m['completed']==m['count'] and m['pnl'] is not None and m['pnl']>0 and m['mean_return']>0 for m in metrics)
    if not economic:return False
    if p['macro_score']+1e-9<r['macro_score']:return False
    if p['nontrend_score'] is not None and r['nontrend_score'] is not None and p['nontrend_score']+1e-9<r['nontrend_score']:return False
    return all(p['groups'][k]['value']>=v['value']-5-1e-9 for k,v in r['groups'].items())


def rank(metrics, cid):
    return (min(m['dual_payoff'] for m in metrics),min(m['macro_score'] for m in metrics),metrics[0]['mean_return'] or -1e100,cid)
