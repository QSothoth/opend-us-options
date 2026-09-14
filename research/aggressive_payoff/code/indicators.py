"""Causal OHLCV features. No OpenD or option-market data dependency.

Continuous indicators carry completed RTH bars across days, seeded only from
available Release history. Session VWAP, anchors and retest state reset daily.
Profile uses strictly PREVIOUS session, allocating each bar's volume uniformly
across intersecting price bins. This is an OHLCV approximation, not tick profile.
"""
import numpy as np
import pandas as pd
from data import SYMBOLS,roll


def lag(a,n=1):
    return np.concatenate([np.full((len(a),n),np.nan),a[:,:-n]],axis=1)


def wilder(x,n):
    x=np.asarray(x,float);out=np.full_like(x,np.nan)
    good=np.flatnonzero(np.isfinite(x))
    if len(good)<n:return out
    # Input leading warm-up NaNs are allowed, interior NaNs fail closed.
    start=good[0]
    if not np.isfinite(x[start:]).all():raise ValueError('interior NaN in continuous Wilder stream')
    seed=start+n-1;out[seed]=np.mean(x[start:seed+1])
    for i in range(seed+1,len(x)):out[i]=((n-1)*out[i-1]+x[i])/n
    return out


class Bank:
    def __init__(self,cube,old):
        self.cube=cube;self.old=old;self.cache={};self.c=cube['close'];self.h=cube['high'];self.l=cube['low'];self.v=cube['volume']
        self.s=np.array([1.,-1.])[None,:,None]
        self.valid=np.isfinite(self.c)
        self.paths=[]
        for sym in SYMBOLS:
            rows=np.flatnonzero(cube['keys'].symbol.to_numpy()==sym)
            rr,cc=np.where(self.valid[rows]);self.paths.append((rows[rr],cc))
    def memo(self,key,fn):
        if key not in self.cache:self.cache[key]=fn()
        return self.cache[key]
    def continuous(self,a,fn):
        out=np.full_like(a,np.nan)
        for r,c in self.paths:out[r,c]=np.asarray(fn(a[r,c]))
        return out
    def rolling(self,a,n,kind='mean'):
        return self.continuous(a,lambda x:getattr(pd.Series(x).rolling(n,min_periods=n),kind)().to_numpy())
    def ema(self,n):
        return self.memo(('ema',n),lambda:self.continuous(self.c,lambda x:pd.Series(x).ewm(span=n,adjust=False,min_periods=n).mean().to_numpy()))
    def atr(self,n):
        def build():
            prev=np.concatenate([self.cube['open'][:,:1],self.c[:,:-1]],axis=1)
            tr=np.maximum(self.h-self.l,np.maximum(abs(self.h-prev),abs(self.l-prev)))
            return self.continuous(tr,lambda x:wilder(x,n))
        return self.memo(('atr',n),build)
    def rsi(self,n):
        def build():
            out=np.full_like(self.c,np.nan)
            for r,c in self.paths:
                x=self.c[r,c];delta=np.r_[np.nan,np.diff(x)]
                up=wilder(np.maximum(delta,0),n);down=wilder(np.maximum(-delta,0),n)
                val=np.divide(100*up,up+down,out=np.full_like(up,50),where=(up+down)!=0)
                out[r,c]=val
            return out
        return self.memo(('rsi',n),build)
    def dmi(self,n):
        def build():
            adx=np.full_like(self.c,np.nan);di=adx.copy()
            for r,c in self.paths:
                h,l,cl=self.h[r,c],self.l[r,c],self.c[r,c]
                up=np.r_[0,np.diff(h)];dn=np.r_[0,-np.diff(l)]
                dm1=np.where((up>dn)&(up>0),up,0);dm2=np.where((dn>up)&(dn>0),dn,0)
                prev=np.r_[cl[0],cl[:-1]]
                tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
                atr=wilder(tr,n);p=100*wilder(dm1,n)/np.maximum(atr,1e-12);m=100*wilder(dm2,n)/np.maximum(atr,1e-12)
                dx=100*abs(p-m)/np.maximum(p+m,1e-12)
                adx[r,c]=wilder(dx,n);di[r,c]=p-m
            return adx,di
        return self.memo(('dmi',n),build)
    def squeeze(self,n,bb,kc):
        def build():
            ma=self.rolling(self.c,n);sd=self.continuous(self.c,lambda x:pd.Series(x).rolling(n,min_periods=n).std(ddof=0).to_numpy())
            mid=self.ema(n);atr=self.atr(n)
            inside=(ma+bb*sd<mid+kc*atr)&(ma-bb*sd>mid-kc*atr)
            # Described LazyBear-style endpoint regression, independently implemented.
            center=(self.rolling(self.h,n,'max')+self.rolling(self.l,n,'min'))/4+ma/2
            detrend=self.c-center
            xx=np.arange(n);weights=1/n+(n-1-xx.mean())*(xx-xx.mean())/np.sum((xx-xx.mean())**2)
            def linear(x):
                out=np.full_like(x,np.nan)
                if len(x)>=n:out[n-1:]=np.lib.stride_tricks.sliding_window_view(x,n)@weights
                return out
            mom=self.continuous(detrend,linear)/np.maximum(atr,1e-12)
            release=lag(inside.astype(float))==1
            release &= ~inside
            return release,mom,inside
        return self.memo(('squeeze',n,bb,kc),build)
    def profile(self,bins,area,source=None):
        def build():
            use=self.cube if source is None else source
            profiles={};result=np.full((len(self.c),3),np.nan)
            for i,row in use['keys'].iterrows():
                valid=np.isfinite(use['close'][i]);lo=use['low'][i,valid];hi=use['high'][i,valid];vol=use['volume'][i,valid]
                # Excludes padding; source session close is fixed by exchange calendar.
                edges=np.linspace(lo.min(),hi.max()+1e-10,bins+1);hist=np.zeros(bins)
                for a,b,v in zip(lo,hi,vol):
                    i0=max(0,min(bins-1,np.searchsorted(edges,a,side='right')-1));i1=max(i0,min(bins-1,np.searchsorted(edges,b,side='right')-1))
                    hist[i0:i1+1]+=v/(i1-i0+1)
                poc=int(np.argmax(hist));left=right=poc;total=hist[poc]
                while total<area*hist.sum() and (left>0 or right<bins-1):
                    lv=hist[left-1] if left>0 else -1;rv=hist[right+1] if right<bins-1 else -1
                    if rv>lv:right+=1;total+=hist[right]
                    else:left-=1;total+=hist[left]
                profiles[(row.date,row.symbol)]=[(edges[poc]+edges[poc+1])/2,edges[left],edges[right+1]]
            prev={}
            for i,row in self.cube['keys'].iterrows():
                if row.symbol in prev:result[i]=profiles.get(prev[row.symbol],[np.nan]*3)
                prev[row.symbol]=(row.date,row.symbol)
            return result
        return self.memo(('profile',bins,area,id(source)),build)
    def rs(self,n,normalized):
        def build():
            delta=self.c-lag(self.c,n)
            ret=delta/np.maximum(self.atr(14),1e-12) if normalized else delta/lag(self.c,n)*100
            keys=self.cube['keys'];spy=ret[keys.symbol=='US.SPY'];qqq=ret[keys.symbol=='US.QQQ']
            out=ret-np.repeat(spy,10,axis=0);m=keys.symbol.to_numpy()=='US.SPY';out[m]=ret[m]-qqq
            return out
        return self.memo(('rs',n,normalized),build)
    def event_avwap(self,anchor,threshold,opening):
        def build():
            c,h,l,v=self.c,self.h,self.l,self.v;ns,nt=c.shape
            tp=(h+l+c)/3;out=np.full((ns,2,nt),np.nan);age=out.copy()
            for d,sign in enumerate([1,-1]):
                boundary=self.old['orhigh'+str(opening)] if sign==1 else self.old['orlow'+str(opening)]
                event=(sign*(c-boundary)>0) if anchor=='orb' else (self.old['rvol_bar_20']>=threshold)
                event &= np.arange(nt)[None,:]>=opening//5-1
                active=np.zeros(ns,bool);pv=np.zeros(ns);vv=np.zeros(ns);count=np.zeros(ns)
                for j in range(nt):
                    active |= event[:,j]&np.isfinite(c[:,j]);ok=active&np.isfinite(c[:,j]);pv[ok]+=tp[ok,j]*v[ok,j];vv[ok]+=v[ok,j];count[ok]+=1
                    out[ok,d,j]=pv[ok]/np.maximum(vv[ok],1);age[ok,d,j]=count[ok]
            return out,age
        return self.memo(('avwap',anchor,threshold,opening),build)
    def signals(self,family,p,opening=60,profile_source=None):
        """Return (gate, trigger replacement, exit), completed 5m timestamps only."""
        c=self.c[:,None,:];s=self.s;atr=self.atr(14)[:,None,:];prev=lag(self.c)[:,None,:]
        gate=trigger=ex=None
        if family=='vwap_regime':
            vw=self.old['vwap'];delta=self.c-vw
            cross=(np.sign(delta)*np.sign(lag(delta))<0).astype(float)
            count=pd.DataFrame(cross.T).rolling(12,min_periods=1).sum().to_numpy().T
            slope=(vw-lag(vw,p['look']))/np.maximum(self.atr(14),1e-12)
            dist=s*(c-vw[:,None,:])/atr
            gate=(s*slope[:,None,:]>=p['slope'])&(count[:,None,:]<=p['cross'])&(dist>=0)&(dist<=p['cap'])
        elif family=='orb_retest':
            high=self.old['orhigh'+str(opening)];low=self.old['orlow'+str(opening)]
            boundary=np.stack([high,low],axis=1);a=atr[:,0,:];ns,_,nt=boundary.shape
            trigger=np.zeros_like(boundary,bool)
            for d,sign in enumerate([1,-1]):
                started=np.full(ns,-1);touch=np.zeros(ns,bool);confirm=np.zeros(ns,int)
                for j in range(nt):
                    b=boundary[:,d,j];broke=sign*(self.c[:,j]-b)>0.02*self.old['daily_atr_day']
                    expired=(started>=0)&(j-started>p['timeout']);started[expired]=-1;touch[expired]=False;confirm[expired]=0
                    prior=(started>=0)&(j>started)
                    adverse=self.l[:,j] if sign==1 else self.h[:,j]
                    touched=prior&(sign*(adverse-b)<=p['depth']*a[:,j])&(sign*(self.c[:,j]-b)>=-p['depth']*a[:,j])
                    touch |= touched
                    confirm=np.where(touch&broke,confirm+1,0)
                    signal=confirm>=p['confirm']
                    if p['space']:
                        level=self.old['daily_high'] if sign==1 else self.old['daily_low']
                        distance=sign*(level-self.c[:,j])/np.maximum(a[:,j],1e-12)
                        signal &= (distance<=0)|(distance>=p['space'])
                    trigger[:,d,j]=signal
                    new=(started<0)&broke;started[new]=j
        elif family=='ema_pullback':
            fast=self.ema(p['fast'])[:,None,:];slow=self.ema(p['slow'])[:,None,:]
            adverse=np.stack([self.l,self.h],axis=1)
            near=s*(adverse-fast)<=p['depth']*atr
            recent=np.stack([roll(near[:,d,:].astype(float),3,'max')>0 for d in range(2)],axis=1)
            trigger=(s*(fast-slow)>0)&(s*(c-fast)>0)&recent
            if p['slope']:trigger &= s*(slow-lag(slow[:,0,:],3)[:,None,:])>0
            if p['exit']:ex=s*(c-slow)<0
        elif family=='squeeze_momentum':
            release,mom,inside=self.squeeze(p['length'],p['bb'],p['kc'])
            recent=roll(release.astype(float),p['release'],'max')>0
            trigger=recent[:,None,:]&(s*mom[:,None,:]>p['momentum'])&(s*(mom-lag(mom))[:,None,:]>0)
            if p['exit']:ex=s*(mom-lag(mom))[:,None,:]<0
        elif family=='event_avwap':
            av,age=self.event_avwap(p['anchor'],p['event_rvol'],opening)
            dist=s*(c-av)/atr
            trigger=(age>=2)&(age<=p['age'])&(dist>=p['buffer'])&(dist<=p['cap'])
            if p['confirm']==2:
                pav=np.stack([lag(av[:,d,:]) for d in range(2)],axis=1)
                trigger &= s*(prev-pav)>0
        elif family=='volume_profile':
            levels=self.profile(p['bins'],p['area'],profile_source)
            level=np.repeat(levels[:,0,None],2,axis=1) if p['level']=='poc' else np.stack([levels[:,2],levels[:,1]],axis=1)
            trigger=s*(c-level[:,:,None])>p['buffer']*atr
            if p['cross']:trigger &= s*(prev-level[:,:,None])<=p['buffer']*atr
        elif family=='adx_dmi':
            adx,di=self.dmi(p['length']);gate=(adx[:,None,:]>=p['min'])&(s*di[:,None,:]>0)
            if p['rising']:gate &= (adx-lag(adx,3))[:,None,:]>0
            if p['exit']:ex=s*di[:,None,:]<0
        elif family=='rsi_momentum':
            rsi=self.rsi(p['length']);directional=50+s*(rsi[:,None,:]-50)
            gate=(directional>=p['min'])&(directional<=p['max'])
            if p['rising']:gate &= s*(rsi-lag(rsi,2))[:,None,:]>0
            if p['exit']:ex=directional<50
        elif family=='relative_strength':gate=s*self.rs(p['bars'],p['normalized'])[:,None,:]>=p['min']
        return gate,trigger,ex


def market_internals(tick,advances,declines):
    """Explicit synchronized market breadth arrays, never estimated from ten symbols."""
    if tick is None or advances is None or declines is None:raise ValueError('MISSING_DATA: timestamped market-wide TICK/advances/declines required')
    t,a,d=map(lambda x:np.asarray(x,float),(tick,advances,declines))
    if t.shape!=a.shape or t.shape!=d.shape or not np.isfinite([t,a,d]).all():raise ValueError('invalid synchronized breadth inputs')
    return {'tick':t,'add':a-d,'cumulative_tick':np.cumsum(t,axis=-1)}


def gamma_exposure(spot,gamma,signed_dealer_contracts,multiplier=100):
    """Dollar delta change per 1% move for EXPLICIT signed dealer positions.
    Open interest alone does not determine signed dealer inventory.
    """
    if gamma is None or signed_dealer_contracts is None:raise ValueError('MISSING_DATA: chain gamma and signed dealer position/model assumption required')
    g,p=np.asarray(gamma,float),np.asarray(signed_dealer_contracts,float)
    if g.shape!=p.shape or not np.isfinite(g).all() or not np.isfinite(p).all() or spot<=0:raise ValueError('invalid gamma exposure inputs')
    return float(np.sum(g*p)*multiplier*spot**2*.01)
