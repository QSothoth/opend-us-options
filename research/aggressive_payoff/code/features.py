"""Aggressive causal price/volume signals; no option prices or external gate."""
import json,math
import numpy as np
import pandas as pd
from data import roll
from indicators import lag
from previous_features import Features as PreviousFeatures

PROFILE_PARAMS=[
 dict(ema_fast=15,ema_slow=45,rvol_days=10,rvol_min=1.2,adx_minutes=15,adx_min=20,rsi_minutes=15,rsi_min=55,er_minutes=30,er_min=.2,roc_minutes=15,roc_min=.025,bb_minutes=30,bb_expansion=1.1,squeeze_minutes=30,squeeze_kc=1.5,squeeze_release=15,super_minutes=30,super_multiple=1.5,pulse_clv=.70,pulse_body=.4,pulse_range=.025,volume_accel=1.5,rs_minutes=15,rs_min=0,vwap_slope=0,vwap_cross=4),
 dict(ema_fast=45,ema_slow=150,rvol_days=20,rvol_min=1.5,adx_minutes=60,adx_min=25,rsi_minutes=60,rsi_min=60,er_minutes=60,er_min=.35,roc_minutes=30,roc_min=.05,bb_minutes=60,bb_expansion=1.2,squeeze_minutes=60,squeeze_kc=1.5,squeeze_release=30,super_minutes=60,super_multiple=2,pulse_clv=.8,pulse_body=.6,pulse_range=.04,volume_accel=2,rs_minutes=30,rs_min=.00025,vwap_slope=.01,vwap_cross=3),
 dict(ema_fast=15,ema_slow=90,rvol_days=10,rvol_min=2.5,adx_minutes=30,adx_min=35,rsi_minutes=30,rsi_min=70,er_minutes=30,er_min=.5,roc_minutes=15,roc_min=.1,bb_minutes=30,bb_expansion=1.5,squeeze_minutes=90,squeeze_kc=2.5,squeeze_release=60,super_minutes=30,super_multiple=3,pulse_clv=.9,pulse_body=.7,pulse_range=.075,volume_accel=3,rs_minutes=60,rs_min=.00075,vwap_slope=.03,vwap_cross=2)]
FAMILY_KEYS={
 'ema':['ema_fast','ema_slow'],'rvol':['rvol_days','rvol_min'],'adx':['adx_minutes','adx_min'],'rsi':['rsi_minutes','rsi_min'],
 'er':['er_minutes','er_min'],'roc':['roc_minutes','roc_min'],'bb':['bb_minutes','bb_expansion'],
 'squeeze':['squeeze_minutes','squeeze_kc','squeeze_release'],'supertrend':['super_minutes','super_multiple'],
 'pulse':['pulse_clv','pulse_body','pulse_range'],'volume_accel':['volume_accel'],'rs':['rs_minutes','rs_min'],'vwap':['vwap_slope','vwap_cross']}
PACKS=[([],0),(['rvol'],1),(['pulse'],1),(['squeeze'],1),(['supertrend'],1),(['rvol','ema'],2),(['rvol','adx'],2),(['rvol','rsi'],2),(['rvol','roc'],2),(['rvol','bb'],2),(['rvol','volume_accel'],2),(['rvol','ema','roc'],2),(['rvol','adx','squeeze'],2),(['rvol','rs','vwap'],2)]

def params(e):return {**PROFILE_PARAMS[e['profile']],**e.get('indicator_overrides',{})}

class Features(PreviousFeatures):
    def __init__(self,cube,ad,day):
        super().__init__(cube,ad,day);self.aggressive_cache={}
    def n(self,mins):
        if mins%self.step:raise ValueError(f'window {mins} not representable by {self.step}m')
        return int(mins//self.step)
    def gate(self,name,p):
        key=('gate',name,tuple((k,p[k]) for k in FAMILY_KEYS[name]))
        if key in self.aggressive_cache:return self.aggressive_cache[key]
        s,c,a,b=self.s,self.p,self.a,self.bank
        if name=='ema':
            fast=b.ema(self.n(p['ema_fast']));slow=b.ema(self.n(p['ema_slow']));g=(s*(fast-slow)[:,None,:]>0)&(s*(c-fast[:,None,:])>0)
        elif name=='rvol':
            refkey=('rvol_ref',p['rvol_days'])
            if refkey not in self.aggressive_cache:
                ref=np.full_like(self.v,np.nan)
                for sym in self.cube['keys'].symbol.unique():
                    ix=np.flatnonzero(self.cube['keys'].symbol.to_numpy()==sym);ref[ix]=pd.DataFrame(self.v[ix]).shift(1).rolling(p['rvol_days'],min_periods=5).mean().to_numpy()
                self.aggressive_cache[refkey]=ref
            g=np.broadcast_to((self.v/np.maximum(self.aggressive_cache[refkey],1)>=p['rvol_min'])[:,None,:],(len(c),2,c.shape[2]))
        elif name=='adx':
            adx,di=b.dmi(self.n(p['adx_minutes']));g=(adx[:,None,:]>=p['adx_min'])&(s*di[:,None,:]>0)
        elif name=='rsi':g=50+s*(b.rsi(self.n(p['rsi_minutes']))[:,None,:]-50)>=p['rsi_min']
        elif name in ['er','roc']:
            n=self.n(p[name+'_minutes']);delta=self.c-lag(self.c,n);delta[:,n-1]=self.c[:,n-1]-self.cube['open'][:,0]
            if name=='roc':g=s*delta[:,None,:]/a>=p['roc_min']
            else:
                changes=abs(self.c-np.concatenate([self.cube['open'][:,:1],self.c[:,:-1]],axis=1));er=abs(delta)/np.maximum(roll(changes,n,'sum'),1e-12);g=np.broadcast_to((er>=p['er_min'])[:,None,:],(len(c),2,c.shape[2]))
        elif name=='bb':
            n=self.n(p['bb_minutes']);ma=b.rolling(self.c,n);sd=b.memo(('std',n),lambda:b.continuous(self.c,lambda x:pd.Series(x).rolling(n,min_periods=n).std(ddof=0).to_numpy()))
            expansion=sd/np.maximum(lag(sd,self.n(15)),1e-12);g=(expansion[:,None,:]>=p['bb_expansion'])&(s*(c-ma[:,None,:])>0)
        elif name=='squeeze':
            release,mom,_=b.squeeze(self.n(p['squeeze_minutes']),2.,p['squeeze_kc']);recent=roll(release.astype(float),self.n(p['squeeze_release']),'max')>0
            g=recent[:,None,:]&(s*mom[:,None,:]>0)&(s*(mom-lag(mom))[:,None,:]>0)
        elif name=='supertrend':
            atr=b.atr(self.n(p['super_minutes']));trend=np.full_like(self.c,np.nan)
            for rr,cc in b.paths:
                cl=self.c[rr,cc];mid=(self.h[rr,cc]+self.l[rr,cc])/2;aa=atr[rr,cc];up=mid+p['super_multiple']*aa;lo=mid-p['super_multiple']*aa
                valid=np.flatnonzero(np.isfinite(aa));direction=1
                if not len(valid):continue
                start=valid[0];fu,fl=up[start],lo[start]
                for j in range(start,len(cl)):
                    if j>start:
                        fu=up[j] if up[j]<fu or cl[j-1]>fu else fu
                        fl=lo[j] if lo[j]>fl or cl[j-1]<fl else fl
                        if direction==1 and cl[j]<fl:direction=-1
                        elif direction==-1 and cl[j]>fu:direction=1
                    trend[rr[j],cc[j]]=direction
            g=s*trend[:,None,:]>0
        elif name=='pulse':
            ran=np.maximum(self.h-self.l,1e-12);body=self.c-self.cube['open'];clv=(self.c-self.l)/ran
            dclv=np.stack([clv,1-clv],axis=1)
            g=(dclv>=p['pulse_clv'])&(s*body[:,None,:]/ran[:,None,:]>=p['pulse_body'])&(ran[:,None,:]/a>=p['pulse_range'])
        elif name=='volume_accel':
            ref=roll(self.v,self.n(15),'mean',shift=1);g=np.broadcast_to((self.v/np.maximum(ref,1)>=p['volume_accel'])[:,None,:],(len(c),2,c.shape[2]))
        elif name=='rs':
            n=self.n(p['rs_minutes']);ret=(self.c-lag(self.c,n))/lag(self.c,n);keys=self.cube['keys'];spy=ret[keys.symbol=='US.SPY'];qqq=ret[keys.symbol=='US.QQQ'];rs=ret-np.repeat(spy,10,axis=0);m=keys.symbol.to_numpy()=='US.SPY';rs[m]=ret[m]-qqq;g=s*rs[:,None,:]>=p['rs_min']
        elif name=='vwap':
            slope=(self.vw-lag(self.vw,self.n(15)))[:,None,:]/a;delta=self.c-self.vw;cross=(np.sign(delta)*np.sign(lag(delta))<0).astype(float)
            count=pd.DataFrame(cross.T).rolling(self.n(60),min_periods=1).sum().to_numpy().T;g=(s*slope>=p['vwap_slope'])&(count[:,None,:]<=p['vwap_cross'])
        else:raise ValueError(name)
        self.aggressive_cache[key]=g;return g
    def boundary(self,minutes):
        key=('or',minutes)
        if key not in self.aggressive_cache:
            n=self.n(minutes);valid=np.isfinite(self.c);hi=np.maximum.accumulate(np.where(valid,self.h,-np.inf),axis=1);lo=np.minimum.accumulate(np.where(valid,self.l,np.inf),axis=1)
            for x in [hi,lo]:x[:,n:]=x[:,n-1:n];x[:,:n-1]=np.nan
            self.aggressive_cache[key]=np.stack([hi,lo],axis=1)
        return self.aggressive_cache[key]
    def trigger(self,e):
        p=params(e);name=e['trigger'];key=('trigger',name,e['opening_minutes'],e['breakout_buffer'],e['retest_depth'],e['retest_timeout'],e['donchian_minutes'],p['ema_fast'],p['ema_slow'],p['pulse_clv'],p['pulse_body'],p['pulse_range'])
        if key in self.aggressive_cache:return self.aggressive_cache[key]
        s,c,a=self.s,self.p,self.a;bound=self.boundary(e['opening_minutes'])
        if name=='orb':out=s*(c-bound)>e['breakout_buffer']*a
        elif name=='retest':
            ns,_,nt=bound.shape;out=np.zeros_like(bound,bool)
            for d,sign in enumerate([1,-1]):
                started=np.full(ns,-1);touched=np.zeros(ns,bool)
                for j in range(nt):
                    boundary=bound[:,d,j];broke=sign*(self.c[:,j]-boundary)>e['breakout_buffer']*self.ad
                    expired=(started>=0)&((j-started)*self.step>e['retest_timeout']);started[expired]=-1;touched[expired]=False
                    adverse=self.l[:,j] if sign==1 else self.h[:,j]
                    touch=(started>=0)&(j>started)&(sign*(adverse-boundary)<=e['retest_depth']*self.ad)&(sign*(self.c[:,j]-boundary)>=-e['retest_depth']*self.ad)
                    touched|=touch;out[:,d,j]=touched&broke;new=(started<0)&broke;started[new]=j
        elif name=='pullback':
            fast=self.bank.ema(self.n(p['ema_fast']));slow=self.bank.ema(self.n(p['ema_slow']));adverse=np.stack([self.l,self.h],axis=1);near=s*(adverse-fast[:,None,:])<=e['retest_depth']*a
            recent=np.stack([roll(near[:,d].astype(float),self.n(15),'max')>0 for d in range(2)],axis=1);out=(s*(fast-slow)[:,None,:]>0)&(s*(c-fast[:,None,:])>0)&recent
        elif name=='donchian':
            n=self.n(e['donchian_minutes']);level=np.stack([roll(self.h,n,'max',shift=1),roll(self.l,n,'min',shift=1)],axis=1);out=s*(c-level)>e['breakout_buffer']*a
        elif name=='impulse':out=self.gate('pulse',p)
        else:raise ValueError(name)
        self.aggressive_cache[key]=out;return out
    def entry(self,e,cache=True):
        key=json.dumps({k:v for k,v in e.items() if k not in ['entry_deadline','daily_atr_days']},sort_keys=True)
        if cache and key in self.entries:return self.entries[key]
        dist=self.s*(self.p-self.vw[:,None,:]);gate=np.broadcast_to(np.isfinite(self.c)[:,None,:],(len(self.c),2,self.c.shape[1])).copy()
        if e['vwap_mode']!='off':
            gate&=(dist>=0)&(dist<=e['cap_daily_atr']*self.a)
            if e['vwap_mode']=='confirm':
                count=self.n(e['confirmation_minutes'])
                if count>1:
                    for d in range(2):gate[:,d]&=roll((dist[:,d]>0).astype(float),count-1,'min',shift=1)>=1
        gate&=(np.arange(self.c.shape[1])[None,None,:]+1)*self.step>=max(e['warmup_minutes'],e['opening_minutes'])
        gate&=self.trigger(e)
        if e['gates']:
            p=params(e);votes=sum(self.gate(name,p).astype(np.int8) for name in e['gates']);gate&=votes>=e['quorum']
        if cache:self.entries[key]=gate
        return gate
    def for_replay(self,e,rule,style,cache=True):
        f=dict(self.base);f['entry_signal']=self.entry(e,cache=cache);f['atr']=np.broadcast_to(self.ad[:,None],self.c.shape)
        if rule.structure_minutes:
            key=('structure',rule.structure_minutes)
            if key not in self.aggressive_cache:self.aggressive_cache[key]=(roll(self.l,self.n(rule.structure_minutes),'min',shift=1),roll(self.h,self.n(rule.structure_minutes),'max',shift=1))
            f['structural_low'],f['structural_high']=self.aggressive_cache[key]
        if style=='ema':f['indicator_exit']=self.s*(self.p-self.bank.ema(self.n(90))[:,None,:])<-.025*self.a
        return f
