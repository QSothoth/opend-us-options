"""Train-only deterministic broad search, using the production scalar rules."""
import argparse, copy, csv, hashlib, itertools, json, os, random, sys, time
from pathlib import Path
from datetime import timedelta
import numpy as np
from numba import njit

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO))
from custody.adaptive import (AdaptiveIndicators, make_strategy, entry_rule, exit_rule,
    DEFAULT_ENTRY, DEFAULT_EXIT, ENTRY_KEYS, EXIT_KEYS, PROFILES, validate_vectors)
from custody.baseline import session_for
from custody.offline import OfflineMarket
from custody.marketdata import require_paired_bars
from research.custody_v2.data_boundary import training_slice, load_training_cache, bind_cache

fast_entry=njit(entry_rule,cache=True)
fast_exit=njit(exit_rule,cache=True)

@njit(cache=True)
def evaluate(features,prices,next_bar,dtes,e,x,delay):
    out=np.full((len(dtes),8),-1.0)
    for c in range(len(dtes)):
        fs=features[c];pp=prices[c];nb=next_bar[c]
        confirm=0;entry_signal=-1;entry_fill=-1
        flatten=int(x[16])
        for t in range(1,flatten):
            if not np.isfinite(fs[t,0]):confirm=0;continue
            ready=fast_entry(fs[t],e)
            confirm=(confirm+1 if ready else 0) if not fs[t,25] else int(ready)
            if (ready and confirm>=e[12] or t>=e[2]) and pp[t]>0:
                entry_signal=t
                entry_fill=nb[min(t+delay,390)]
                break
        if entry_fill<0 or entry_fill>=390:continue
        ep=fs[entry_fill,1]
        if not np.isfinite(ep):continue
        atr=fs[entry_signal,2]
        state=np.array([ep,float(entry_fill),0.,-1e100])
        decision=-1;reason=0
        for t in range(entry_fill,390):
            if t>=flatten:reason=7
            elif np.isfinite(fs[t,0]):reason=fast_exit(fs[t],x,state,ep,entry_fill,atr,dtes[c])
            if reason>0:decision=t;break
        if decision<0:continue
        intent=nb[decision]
        if intent>=390:continue
        fill=nb[min(intent+delay,390)]
        if fill>=390:continue
        out[c,0]=entry_signal;out[c,1]=entry_fill;out[c,2]=decision
        out[c,3]=intent;out[c,4]=fill;out[c,5]=pp[entry_fill];out[c,6]=pp[fill];out[c,7]=reason
    return out


def load_data(root,out):
    manifest,cases,checked=training_slice(root)
    market=OfflineMarket(root,prefer_csv=True)
    prices=np.zeros((len(cases),391));next_bar=np.full((len(cases),391),390,dtype=np.int64)
    dtes=[];banks=[];sessions=[];underlying=[]
    for i,c in enumerate(cases):
        ss=session_for(c,manifest);sessions.append(ss)
        ub,ob=require_paired_bars(market,c['symbol'],c['contract'],c['trade_date'])
        ub=[b for b in ub if ss.opens<b.close_time<ss.closes]
        if len(ub)!=389:raise ValueError('search requires complete underlying session; never silently interpolate')
        underlying.append(ub)
        for b in ob:
            t=int((b.close_time-ss.opens).total_seconds()/60)
            if 0<t<390 and b.close>0 and b.volume>0:prices[i,t]=b.close
        nxt=390
        for t in range(390,-1,-1):
            if prices[i,t]>0:nxt=t
            next_bar[i,t]=nxt
        from datetime import date
        from custody.opend import parse_option_code
        dtes.append((date.fromisoformat(parse_option_code(c['contract'])[1])-date.fromisoformat(c['trade_date'])).days)
    for p in range(len(PROFILES)):
        ff=np.full((len(cases),391,31),np.nan)
        strategy=make_strategy(p)
        for i,c in enumerate(cases):
            engine=AdaptiveIndicators(strategy,c['symbol'],c['direction'],sessions[i])
            for b in underlying[i]:
                f=engine.step(b);v=f.diagnostics['vector'];ff[i,int(v[0])]=v
        banks.append(ff)
        print('feature profile',p,'ready',flush=True)
    features=np.array(banks);dtes=np.array(dtes)
    dates=sorted({c['trade_date'] for c in cases})
    blocks=np.array([0 if c['trade_date'] in dates[:6] else 1 if c['trade_date'] in dates[6:12] else 2 for c in cases])
    metadata={'cases':cases,'dates':dates,'source':'custody-train-dte4','checksums_verified':checked}
    (out/'cache_metadata.json').write_text(json.dumps(metadata,indent=2))
    np.savez_compressed(out/'cache.npz',features=features,prices=prices,next_bar=next_bar,dtes=dtes,blocks=blocks)
    bind_cache(out)
    return features,prices,next_bar,dtes,blocks,metadata


def stats(trades,blocks,slip=25,fee=.65):
    complete=trades[:,0]>=0
    n=int(complete.sum())
    a=trades[complete,5]*(1+slip/10000)
    b=trades[complete,6]*(1-slip/10000)
    dollars=(b-a)*100-2*fee
    returns=dollars/(a*100)
    def ratio(v):
        wins=v[v>1e-10];losses=v[v< -1e-10]
        return float(wins.mean()/(-losses.mean())) if len(wins) and len(losses) else 0.
    r1=ratio(dollars);r2=ratio(returns)
    block_means=[float(returns[blocks[complete]==k].mean()) if np.any(blocks[complete]==k) else -999 for k in (0,1,2)]
    return dict(completed=n,pnl=float(dollars.sum()) if n else -1e9,mean=float(returns.mean()) if n else -999,
       payoff_dollar=r1,payoff_return=r2,dual_payoff=min(r1,r2),wins=int((dollars>1e-10).sum()),
       avg_win=float(returns[returns>1e-10].mean()) if np.any(returns>1e-10) else 0,
       avg_loss=float(-returns[returns< -1e-10].mean()) if np.any(returns< -1e-10) else 0,
       blocks=block_means,block_positive=sum(v>0 for v in block_means),
       eligible=bool(n==len(blocks) and dollars.sum()>0 and returns.mean()>0 and r1>0 and r2>0))


ED={0:list(range(11)),1:[10,15,20,30],2:[45,60,90,120],3:[20,30,45],4:[3,4,5],5:[2,3,4],6:[0,5,10],
    7:[15,20,25],8:[.8,1.1,1.5,2],9:[0,.5,1,2,4],10:[0,.2,.35],11:[-99,0,.25],12:[1,2,3],13:[.25,.5,1],14:[0,.25,.5]}
XD={0:[4,6,8],1:[0,.25,.5,1,2,3],2:[0,3,5,10],3:[0,.5,1,2,4],4:[0,5,10,20,30,60],5:[0,.5,1,2],6:[0,1,2,3],
    7:[0,1,2,4],8:[0,1,2,4,6],9:[0,1,2,4,6,8],10:[0,4,8],11:[4,6,8,12],12:[0,5,10,20],
    13:[0,1,2,4],14:[0,10,20,30,60],15:[-.25,0,.25],16:[360,370,375],17:[5,10,20],18:[.5,.75,1],19:[0,1],20:[0,1],21:[0,1,2]}

def config_id(p,e,x):return hashlib.sha256(json.dumps([p,e,x],separators=(',',':')).encode()).hexdigest()[:16]

def proposals():
    rng=random.Random(20260915);seen=set();items=[]
    def add(p,e,x,family):
        e=list(e);x=list(x)
        if e[3]>e[2]:e[3]=e[2]
        if e[1]>e[2]:return
        validate_vectors(e,x)
        uid=config_id(p,e,x)
        if uid not in seen:items.append(dict(id=uid,profile=p,e=e,x=x,family=family));seen.add(uid)
    add(1,DEFAULT_ENTRY,DEFAULT_EXIT,'corrected_reference')
    # One-factor exit coverage and bounded early-failure/trailing combinations.
    for k,vals in XD.items():
        for v in vals:
            x=list(DEFAULT_EXIT);x[k]=v;add(1,DEFAULT_ENTRY,x,'exit_one_factor')
    for soft,fail,escape,trail,activation in itertools.product([0,.5,1,2],[5,10,20,30],[0,1,2],[2,4,8],[1,4]):
        x=list(DEFAULT_EXIT);x[1]=soft;x[4]=fail;x[3]=escape;x[7]=escape;x[9]=trail;x[8]=activation
        add(1,DEFAULT_ENTRY,x,'exit_grid')
    for p,mode,warm,deadline,cap in itertools.product(range(6),range(11),[15,30],[45,90],[0,1,3]):
        e=list(DEFAULT_ENTRY);e[0]=mode;e[1]=warm;e[2]=deadline;e[9]=cap
        add(p,e,DEFAULT_EXIT,'entry_grid')
    for n in range(12000):
        p=rng.randrange(6);e=[rng.choice(ED[k]) for k in range(len(ENTRY_KEYS))];x=[rng.choice(XD[k]) for k in range(len(EXIT_KEYS))]
        # Sparse draws preserve interpretability and avoid every exit acting at once.
        for k in (9,10):
            if rng.random()<.5:e[k]=0
        if rng.random()<.5:e[11]=-99
        for k in (1,10,12,14):
            if rng.random()<.5:x[k]=0
        add(p,e,x,'broad_joint')
    return items

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--train',required=True);ap.add_argument('--out',required=True);ap.add_argument('--phase',default='search',choices=['search','pressure'])
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    if (out/'cache.npz').exists():
        features,prices,next_bar,dtes,blocks,metadata=load_training_cache(a.train,out)
    else:features,prices,next_bar,dtes,blocks,metadata=load_data(a.train,out)
    rows=[];traces=[];configs=[]
    def run(items,stage):
        started=time.monotonic()
        for j,c in enumerate(items):
            trades=evaluate(features[c['profile']],prices,next_bar,dtes,np.array(c['e'],dtype=float),np.array(c['x'],dtype=float),1)
            m=stats(trades,blocks);row={**c,**m,'stage':stage};rows.append(row);traces.append(trades);configs.append(c)
            if (j+1)%1000==0:print(stage,j+1,'/',len(items),'seconds',round(time.monotonic()-started,1),flush=True)
    if a.phase=='search':
        items=proposals()
        (out/'CANDIDATES_BEFORE_RESULTS.json').write_text(json.dumps(items,separators=(',',':')))
        (out/'PARAMETER_DOMAINS.json').write_text(json.dumps({'entry_keys':ENTRY_KEYS,'exit_keys':EXIT_KEYS,'entry':ED,'exit':XD,'profiles':PROFILES},indent=2))
        print('frozen broad candidates',len(items),flush=True);run(items,'broad')
        eligible=sorted([r for r in rows if r['eligible']],key=lambda r:(r['dual_payoff'],r['mean'],r['id']),reverse=True)
        leaders=[]
        for r in eligible:
            signature=(r['profile'],int(r['e'][0]))
            if signature not in [(z['profile'],int(z['e'][0])) for z in leaders]:leaders.append(r)
            if len(leaders)>=12:break
        if not leaders:raise ValueError('no eligible broad candidate; keep full evidence')
        seen={c['id'] for c in configs};local=[]
        for lead in leaders:
            for side,domain in [('e',ED),('x',XD)]:
                for k,vs in domain.items():
                    for v in vs:
                        c={q:copy.deepcopy(lead[q]) for q in ('profile','e','x')};c[side][k]=v
                        if c['e'][3]>c['e'][2]:continue
                        try:validate_vectors(c['e'],c['x'])
                        except ValueError:continue
                        uid=config_id(c['profile'],c['e'],c['x'])
                        if uid in seen:continue
                        seen.add(uid);c.update(id=uid,family='local_neighbor',parent=lead['id']);local.append(c)
        local=local[:2000]
        (out/'LOCAL_CANDIDATES_BEFORE_RESULTS.json').write_text(json.dumps(local,separators=(',',':')))
        run(local,'local')
        (out/'all_results.json').write_text(json.dumps(rows,separators=(',',':'),allow_nan=False))
        np.savez_compressed(out/'all_trades.npz',trades=np.array(traces))
        eligible=sorted([r for r in rows if r['eligible']],key=lambda r:(r['dual_payoff'],r['mean'],r['id']),reverse=True)
        shortlist={r['id']:r for r in eligible[:80]}
        for r in sorted(eligible,key=lambda r:(r['block_positive'],min(r['blocks']),r['dual_payoff']),reverse=True)[:40]:shortlist[r['id']]=r
        for r in sorted(eligible,key=lambda r:r['mean'],reverse=True)[:40]:shortlist[r['id']]=r
        (out/'SHORTLIST_BEFORE_PRESSURE.json').write_text(json.dumps(list(shortlist.values()),indent=2))
        print('search_done',len(rows),'eligible',len(eligible),'shortlist',len(shortlist),flush=True)
        print('nominal',json.dumps(eligible[0]),flush=True)
    else:
        short=json.loads((out/'SHORTLIST_BEFORE_PRESSURE.json').read_text());ranked=[]
        for c in short:
            scenarios=[]
            for delay,bps in [(1,25),(2,25),(3,25),(1,50),(1,100)]:
                tr=evaluate(features[c['profile']],prices,next_bar,dtes,np.array(c['e'],float),np.array(c['x'],float),delay)
                m=stats(tr,blocks,bps);scenarios.append(dict(delay=delay,bps=bps,**m))
            passing=sum(m['eligible'] for m in scenarios)+c['block_positive']
            ranked.append({**c,'scenarios':scenarios,'conditions_passed':passing,'robust_feasible':passing==8,
                'worst_dual_payoff':min(m['dual_payoff'] for m in scenarios)})
        ranked.sort(key=lambda r:(r['conditions_passed'],r['worst_dual_payoff'],r['mean'],r['id']),reverse=True)
        (out/'pressure_results.json').write_text(json.dumps(ranked,indent=2))
        (out/'SELECTED_BEFORE_SERVICE_REPLAY.json').write_text(json.dumps(ranked[0],indent=2))
        print('pressure_done',len(ranked),'robust_feasible',sum(r['robust_feasible'] for r in ranked),flush=True)
        print('selected',json.dumps(ranked[0]),flush=True)

if __name__=='__main__':main()
