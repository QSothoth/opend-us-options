"""Same scalar decisions as production; optional Numba is research-only."""
import numpy as np
from numba import njit
from custody.regime import route_step
from research.custody_v2.search import fast_entry, fast_exit

fast_route = njit(route_step, cache=True)


@njit(cache=True)
def evaluate(features, prices, next_bar, dtes, router, entries, exits, delay):
    out = np.full((len(dtes),10), -1.)
    for c in range(len(dtes)):
        fs,pp,nb=features[c],prices[c],next_bar[c]
        rs=np.array([0.,0.,0.,1000.,1000.])
        confirm=0;prior=-1;entry_signal=-1;entry_fill=-1;entry_state=-1;switches=0
        flatten=int(exits[0,16])
        for t in range(1,flatten):
            if not np.isfinite(fs[t,0]):confirm=0;continue
            active=fast_route(fs[t],router,rs)
            if active!=prior:confirm=0;switches+=int(prior>=0)
            prior=active;e=entries[active]
            ready=fast_entry(fs[t],e) and active!=4 and (active!=0 or router[7]>0)
            confirm=(confirm+1 if ready else 0) if not fs[t,25] else int(ready)
            if (ready and confirm>=e[12] or t>=e[2]) and pp[t]>0:
                entry_signal=t;entry_fill=nb[min(t+delay,390)];entry_state=active;break
        if entry_fill<0 or entry_fill>=390:continue
        ep=fs[entry_fill,1];atr=fs[entry_signal,2]
        if not np.isfinite(ep):continue
        state=np.array([ep,float(entry_fill),0.,-1e100])
        # Indicators keep observing completed bars while a buy is awaiting fill.
        for t in range(entry_signal+1,entry_fill):
            if np.isfinite(fs[t,0]):
                active=fast_route(fs[t],router,rs);switches+=int(active!=prior);prior=active
        decision=-1;reason=0;exit_prior=-1
        for t in range(entry_fill,390):
            if np.isfinite(fs[t,0]):
                active=fast_route(fs[t],router,rs);switches+=int(active!=prior);prior=active
                if active!=exit_prior:state[2]=0.
                exit_prior=active
            if t>=flatten:reason=7
            elif np.isfinite(fs[t,0]):reason=fast_exit(fs[t],exits[active],state,ep,entry_fill,atr,dtes[c])
            if reason>0:decision=t;break
        if decision<0:continue
        intent=nb[decision]
        if intent>=390:continue
        fill=nb[min(intent+delay,390)]
        if fill>=390:continue
        out[c,:8]=np.array([entry_signal,entry_fill,decision,intent,fill,pp[entry_fill],pp[fill],reason])
        out[c,8]=entry_state;out[c,9]=switches
    return out


@njit(cache=True)
def scheduled(prices,next_bar,entry_times,holds,delay=1):
    """Ex-ante clock plans; -1 remains an incomplete round trip, never stale NAV."""
    n,k=entry_times.shape
    out=np.full((n,k,8),-1.)
    for c in range(n):
        for j in range(k):
            signal=next_bar[c,entry_times[c,j]]
            if signal>=390:continue
            entry=next_bar[c,min(signal+delay,390)]
            if entry>=390:continue
            decision=min(entry+holds[c,j],360)
            if decision<=entry:continue
            intent=next_bar[c,decision]
            if intent>=390:continue
            exit_=next_bar[c,min(intent+delay,390)]
            if exit_>=390:continue
            out[c,j,:]=np.array([signal,entry,decision,intent,exit_,prices[c,entry],prices[c,exit_],7.])
    return out
