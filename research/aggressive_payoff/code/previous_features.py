"""Causal native-bar indicator combinations; shared prior-day ATR price budgets."""
from dataclasses import replace
import numpy as np
import pandas as pd
from data import roll
from engine import native_features,Rule
from indicators import Bank,lag

FAMILIES=['vwap','ema','adx','rsi','rvol','er','bb','squeeze','avwap','profile','rs','daily','failed_level']
PAIRS=[['ema','rvol'],['adx','er'],['vwap','rs'],['ema','adx'],['rsi','rvol'],['squeeze','rvol'],['bb','rs'],['avwap','adx'],['profile','ema'],['daily','rsi']]
TRIPLES=[['ema','adx','rsi'],['vwap','rs','rvol'],['ema','er','bb'],['avwap','profile','daily']]

class Features:
    def __init__(self,cube,ad,day):
        self.cube=cube;self.ad=ad;self.step=cube['step'];self.bank=Bank(cube,{})
        self.c=cube['close'];self.h=cube['high'];self.l=cube['low'];self.v=cube['volume']
        self.s=np.array([1.,-1.])[None,:,None];self.p=self.c[:,None,:];self.a=ad[:,None,None]
        self.base=native_features(cube,ad,Rule(signal_minutes=self.step,vwap_max_atr=1e9))
        self.vw=self.base['vwap'];self.cache={};self.entries={}
        daily=[]
        for sym,g in day.groupby('symbol'):
            g=g.sort_values('time').copy()
            for n in [20,50]:g[f'ema{n}']=g.close.ewm(span=n,adjust=False,min_periods=n).mean()
            cols=['high','low','close','ema20','ema50'];x=g[cols].shift(1);x['date']=g.date;x['symbol']=sym;daily.append(x)
        self.prev=cube['keys'][['date','symbol']].merge(pd.concat(daily),on=['date','symbol'],validate='one_to_one')
    def n(self,mins):
        assert mins%self.step==0
        return mins//self.step
    def gate(self,name,tier):
        key=(name,tier)
        if key in self.cache:return self.cache[key]
        s,c,a,b=self.s,self.p,self.a,self.bank;k=tier
        if name=='vwap':
            look=self.n([15,30][k]);slope=(self.vw-lag(self.vw,look))[:,None,:]/a
            delta=self.c-self.vw;cross=(np.sign(delta)*np.sign(lag(delta))<0).astype(float)
            count=pd.DataFrame(cross.T).rolling(self.n(60),min_periods=1).sum().to_numpy().T
            gate=(s*slope>=[0,.01][k])&(count[:,None,:]<=[4,2][k])
        elif name=='ema':
            fast=b.ema(self.n([30,45][k]));slow=b.ema(self.n([90,150][k]))
            gate=(s*(fast-slow)[:,None,:]>0)&(s*(c-fast[:,None,:])>0)
        elif name=='adx':
            adx,di=b.dmi(self.n([60,90][k]));gate=(adx[:,None,:]>=[15,25][k])&(s*di[:,None,:]>0)
        elif name=='rsi':
            val=50+s*(b.rsi(self.n([60,90][k]))[:,None,:]-50)
            gate=(val>=[50,55][k])&(val<=[85,80][k])
        elif name=='rvol':
            ref=np.full_like(self.v,np.nan)
            for sym in self.cube['keys'].symbol.unique():
                ix=np.flatnonzero(self.cube['keys'].symbol.to_numpy()==sym)
                ref[ix]=pd.DataFrame(self.v[ix]).shift(1).rolling([10,20][k],min_periods=5).mean().to_numpy()
            gate=np.broadcast_to((self.v/np.maximum(ref,1)>=[1.,1.5][k])[:,None,:],(len(c),2,c.shape[2]))
        elif name=='er':
            n=self.n([30,60][k]);change=abs(self.c-lag(self.c));er=abs(self.c-lag(self.c,n))/np.maximum(roll(change,n,'sum'),1e-12)
            gate=np.broadcast_to((er>=[.2,.4][k])[:,None,:],(len(c),2,c.shape[2]))
        elif name=='bb':
            n=self.n([60,90][k]);ma=b.rolling(self.c,n);sd=b.continuous(self.c,lambda x:pd.Series(x).rolling(n,min_periods=n).std(ddof=0).to_numpy())
            expansion=sd/np.maximum(lag(sd,self.n(15)),1e-12)
            gate=(expansion[:,None,:]>=[1.05,1.15][k])&(s*(c-ma[:,None,:])>0)
        elif name=='squeeze':
            release,mom,inside=b.squeeze(self.n([60,90][k]),2.,[1.5,2.][k]);recent=roll(release.astype(float),self.n([30,60][k]),'max')>0
            gate=recent[:,None,:]&(s*mom[:,None,:]>0)&(s*(mom-lag(mom))[:,None,:]>0)
        elif name=='avwap':
            ns,nt=self.c.shape;out=np.full((ns,2,nt),np.nan);age=out.copy();tp=(self.h+self.l+self.c)/3
            bound=np.stack([self.base['opening_high'],self.base['opening_low']],axis=1)
            for d,sign in enumerate([1,-1]):
                active=np.zeros(ns,bool);pv=np.zeros(ns);vv=np.zeros(ns);clock=np.zeros(ns)
                for j in range(nt):
                    active|=(sign*(self.c[:,j]-bound[:,d,j])>.02*self.ad)
                    ok=active&np.isfinite(self.c[:,j]);pv[ok]+=tp[ok,j]*self.v[ok,j];vv[ok]+=self.v[ok,j];clock[ok]+=self.step
                    out[ok,d,j]=pv[ok]/np.maximum(vv[ok],1);age[ok,d,j]=clock[ok]
            dist=s*(c-out)/a;gate=(age>=15)&(age<=[120,60][k])&(dist>=[0,.01][k])&(dist<=[.2,.125][k])
        elif name=='profile':
            levels=b.profile([24,48][k],.7)
            level=np.repeat(levels[:,0,None],2,axis=1) if k==0 else np.stack([levels[:,2],levels[:,1]],axis=1)
            gate=s*(c-level[:,:,None])>=0
        elif name=='rs':
            n=self.n([30,60][k]);ret=(self.c-lag(self.c,n))/lag(self.c,n)
            keys=self.cube['keys'];spy=ret[keys.symbol=='US.SPY'];qqq=ret[keys.symbol=='US.QQQ'];rs=ret-np.repeat(spy,10,axis=0);m=keys.symbol.to_numpy()=='US.SPY';rs[m]=ret[m]-qqq
            gate=s*rs[:,None,:]>=[0,.0005][k]
        elif name=='daily':
            dist=(self.prev.close-self.prev[f'ema{[20,50][k]}']).to_numpy();gate=np.broadcast_to(s*dist[:,None,None]>0,(len(c),2,c.shape[2]))
        elif name=='failed_level':
            level=np.stack([self.prev.low.to_numpy(),self.prev.high.to_numpy()],axis=1)[:,:,None]
            adverse=np.stack([self.l,self.h],axis=1);touch=s*(adverse-level)<-[0,.02][k]*a
            seen=np.maximum.accumulate(touch,axis=2);gate=seen&(s*(c-level)>[0,.01][k]*a)
        else:raise ValueError(name)
        self.cache[key]=gate;return gate
    def trigger(self,name):
        key=('trigger',name)
        if key in self.cache:return self.cache[key]
        s,c,a=self.s,self.p,self.a
        boundary=np.stack([self.base['opening_high'],self.base['opening_low']],axis=1)
        if name=='orb':out=s*(c-boundary)>.02*a
        elif name=='retest':
            ns,_,nt=boundary.shape;out=np.zeros_like(boundary,bool)
            for d,sign in enumerate([1,-1]):
                started=np.full(ns,-1);touched=np.zeros(ns,bool)
                for j in range(nt):
                    b=boundary[:,d,j];broke=sign*(self.c[:,j]-b)>.02*self.ad
                    expired=(started>=0)&((j-started)*self.step>60);started[expired]=-1;touched[expired]=False
                    adverse=self.l[:,j] if sign==1 else self.h[:,j]
                    touch=(started>=0)&(j>started)&(sign*(adverse-b)<=.04*self.ad)&(sign*(self.c[:,j]-b)>=-.04*self.ad)
                    touched|=touch;out[:,d,j]=touched&broke
                    new=(started<0)&broke;started[new]=j
        elif name=='pullback':
            fast=self.bank.ema(self.n(30));slow=self.bank.ema(self.n(90));adverse=np.stack([self.l,self.h],axis=1)
            near=s*(adverse-fast[:,None,:])<=.04*a
            recent=np.stack([roll(near[:,d,:].astype(float),self.n(15),'max')>0 for d in range(2)],axis=1)
            out=(s*(fast-slow)[:,None,:]>0)&(s*(c-fast[:,None,:])>0)&recent
        else:raise ValueError(name)
        self.cache[key]=out;return out
    def entry(self,spec):
        import json
        key=json.dumps(spec,sort_keys=True)
        if key in self.entries:return self.entries[key]
        dist=self.s*(self.p-self.vw[:,None,:]);gate=(dist>=0)&(dist<=spec['cap_daily_atr']*self.a)&np.isfinite(self.base['atr'][:,None,:])
        count=self.n(15)
        if count>1:
            for d in range(2):gate[:,d]&=roll((dist[:,d]>0).astype(float),count-1,'min',shift=1)>=1
        gate&=self.trigger(spec['trigger'])
        if spec['gates']:
            votes=sum(self.gate(name,spec['tier']).astype(np.int8) for name in spec['gates'])
            gate&=votes>=spec['quorum']
        self.entries[key]=gate;return gate
    def for_replay(self,entry,rule,exit_style):
        f=dict(self.base);f['entry_signal']=self.entry(entry);f['atr']=np.broadcast_to(self.ad[:,None]/8,self.c.shape)
        if rule.structure_minutes:
            key=('structure',rule.structure_minutes)
            if key not in self.cache:self.cache[key]=(roll(self.l,self.n(rule.structure_minutes),'min',shift=1),roll(self.h,self.n(rule.structure_minutes),'max',shift=1))
            f['structural_low'],f['structural_high']=self.cache[key]
        if exit_style=='ema_slow':f['indicator_exit']=self.s*(self.p-self.bank.ema(self.n(90))[:,None,:])<-.025*self.a
        elif exit_style=='dmi_flip':f['indicator_exit']=self.s*self.bank.dmi(self.n(60))[1][:,None,:]<0
        return f
