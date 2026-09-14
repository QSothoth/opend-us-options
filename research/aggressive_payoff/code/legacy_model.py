"""One human JobSpec/day, vectorized independent replicas. Never a portfolio."""
from __future__ import annotations
from dataclasses import dataclass,field,asdict
from enum import IntEnum
import hashlib,json
import math
import numpy as np
import pandas as pd
from data import SYMBOLS

class State(IntEnum):
    IDLE=0; WATCH=1; ENTRY=2; IN=3; EXIT=4; DONE=5

@dataclass(frozen=True)
class EntryRule:
    kind:str='breakout'
    opening_minutes:int=60
    start_et:str='10:30'
    deadline_et:str='14:00'
    buffer_daily_atr:float=.02
    er_minutes:int=60
    er_min:float=0
    rvol_days:int=20
    rvol_mode:str='cumulative'
    rvol_min:float=0
    vwap_max_atr:float=0
    vwap_confirmation:int=1
    compression_quantile:float=0
    expansion_min:float=1.1
    daily_ema:int=0
    daily_slope_days:int=3
    rs_minutes:int=20
    rs_min:float=-999
    reclaim_depth_atr:float=.25
    reclaim_confirmation:int=1
    def __post_init__(self):
        if self.kind not in ['breakout','reclaim']:raise ValueError('unknown entry kind')
        if self.opening_minutes not in [30,45,60,75,90]:raise ValueError('unsupported opening window')
        if not 570<=minute(self.start_et)<=minute(self.deadline_et)<=945:raise ValueError('entry window invalid')
        if self.er_minutes not in [30,60,90] or self.rvol_days not in [10,20] or self.rvol_mode not in ['bar','cumulative']:raise ValueError('unsupported feature history')
        if self.daily_ema not in [0,20,50] or self.daily_slope_days not in [1,3] or self.rs_minutes not in [10,20,40]:raise ValueError('unsupported context')
        if self.vwap_confirmation not in [1,2] or self.reclaim_confirmation not in [1,2] or self.compression_quantile not in [0,.2,.4]:raise ValueError('unsupported confirmation/quantile')
        for name in ['buffer_daily_atr','er_min','rvol_min','vwap_max_atr','expansion_min','reclaim_depth_atr']:
            if not math.isfinite(getattr(self,name)) or getattr(self,name)<0:raise ValueError(name)
        if not math.isfinite(self.rs_min):raise ValueError('invalid relative strength')

@dataclass(frozen=True)
class ExitRule:
    trail_mode:str='daily'
    trailing_atr:float=.35
    atr_minutes:int=70
    max_hold_minutes:int=60
    hard_stop_daily_atr:float=.8
    structural_minutes:int=0
    structural_buffer_atr:float=0
    failure_minutes:int=0
    profit_activation_daily_atr:float=0
    profit_keep:float=0
    def __post_init__(self):
        if self.trail_mode not in ['daily','intraday'] or self.atr_minutes not in [35,70]:raise ValueError('invalid ATR reference')
        if self.structural_minutes not in [0,10,15,20]:raise ValueError('invalid structural window')
        if type(self.max_hold_minutes)!=int or not 1<=self.max_hold_minutes<=390:raise ValueError('invalid holding duration')
        if type(self.failure_minutes)!=int or not 0<=self.failure_minutes<=390:raise ValueError('invalid failure duration')
        for name in ['trailing_atr','hard_stop_daily_atr']:
            if not math.isfinite(getattr(self,name)) or getattr(self,name)<=0:raise ValueError(name)
        for name in ['structural_buffer_atr','profit_activation_daily_atr','profit_keep']:
            if not math.isfinite(getattr(self,name)) or getattr(self,name)<0:raise ValueError(name)
        if self.profit_keep>=1 or bool(self.profit_keep)!=bool(self.profit_activation_daily_atr):raise ValueError('invalid observed profit floor')

def minute(s):
    h,m=map(int,s.split(':'))
    if not 0<=h<24 or not 0<=m<60:raise ValueError(s)
    return h*60+m

@dataclass(frozen=True)
class JobSpec:
    symbol:str
    direction:str
    contract:str|None=None
    max_qty:int=1
    entry_rule:EntryRule=field(default_factory=EntryRule)
    exit_rule:ExitRule=field(default_factory=ExitRule)
    flatten_et:str='15:45'
    dry_run:bool=True
    def __post_init__(self):
        s='US.'+self.symbol.upper().removeprefix('US.')
        if s not in SYMBOLS or self.direction not in ['LONG','SHORT']:raise ValueError('human symbol/direction required')
        object.__setattr__(self,'symbol',s)
        if self.dry_run is not True or type(self.max_qty)!=int or self.max_qty<1:raise ValueError('offline JobSpec only')
        if not 630<=minute(self.flatten_et)<=945:raise ValueError('invalid flatten')

def rule_id(entry,exit):
    return hashlib.sha256(json.dumps({'entry_rule':asdict(entry),'exit_rule':asdict(exit)},sort_keys=True).encode()).hexdigest()[:12]

REASONS={0:'no_entry',1:'scheduled_flatten',2:'hard_stop_gap',3:'hard_stop_intrabar',4:'trailing',5:'max_hold',6:'structural',7:'failed_entry',8:'profit_protection',9:'indicator_exit'}

def replay(cube5, features, entry=EntryRule(), exit=ExitRule(), execution=None,
           fine_entry=False,fine_exit=False,job=None,trace=False,
           entry_gate=None,entry_override=None,exit_signal=None,indicator_exit_grace=10):
    """Close decisions -> next grid open. Resting hard stop only intrabar fill.

    1m mode reads as-of COMPLETED 5m features; no interpolation of 5m OHLC.
    fine_entry/fine_exit allow testing 1m closing prices against those features.
    Every row remains one independent human-selected counterfactual JobSpec.
    """
    cube=execution if execution is not None else cube5
    keys=cube['keys'];base=cube5['keys']
    ix=base.reset_index().merge(keys[['date','symbol']],on=['date','symbol'],how='right',validate='one_to_one')['index'].to_numpy()
    repeats=np.repeat(np.arange(len(keys)),2)
    meta=keys.iloc[repeats].reset_index(drop=True).copy();meta['direction']=np.tile(['LONG','SHORT'],len(keys))
    source=np.repeat(ix,2);barix=repeats
    if job:
        entry,exit=job.entry_rule,job.exit_rule
        keep=((meta.symbol==job.symbol)&(meta.direction==job.direction)).to_numpy()
        source,barix=source[keep],barix[keep];meta=meta[keep].reset_index(drop=True)
    n=len(meta);sign=np.where(meta.direction.to_numpy()=='LONG',1.,-1.)
    direction_index=np.where(sign==1,0,1)
    for signal in [entry_gate,entry_override,exit_signal]:
        if signal is not None and signal.shape != (len(base),2,78):raise ValueError('signal shape must be sessions x directions x 78 completed 5m bars')
    def custom(signal,j):return signal[source,direction_index,j]
    state=np.full(n,State.WATCH,dtype=np.int8)
    en=np.full(n,np.nan);xp=en.copy();stop=en.copy();best=en.copy()
    ei=np.full(n,-1);xi=ei.copy();signal_i=ei.copy();exit_signal_i=ei.copy()
    reason=np.zeros(n,dtype=np.int8);pending=reason.copy();breached=np.zeros(n,bool);reclaims=np.zeros(n,dtype=int)
    hist=[]
    flatten=meta.flatten.to_numpy()
    if job:flatten=np.minimum(flatten,minute(job.flatten_et))
    step=cube['step'];ad=features['daily_atr_day'][source]
    raw={c:cube[c][barix] for c in ['open','high','low','close']}
    def feat(name,j):
        a=features[name]
        return a[source] if a.ndim==1 else a[source,j]
    def close_trade(mask,price,i,why):
        nonlocal state
        xp[mask]=price[mask] if np.ndim(price) else price;xi[mask]=i
        reason[mask]=why[mask] if np.ndim(why) else why;state[mask]=State.DONE
    for i in range(raw['open'].shape[1]):
        clock=570+i*step;end=clock+step
        o,h,l,c=(raw[x][:,i] for x in ['open','high','low','close'])
        valid=np.isfinite(o)
        # Predetermined flatten executes on the clock, before this bar's HLC.
        mask=(state==State.IN)|(state==State.EXIT)
        close_trade(mask&(clock>=flatten)&valid,o,i,1)
        state[(state!=State.DONE)&(clock>=flatten)]=State.DONE
        pend=(state==State.EXIT)&valid
        close_trade(pend,o,i,pending)
        enter=(state==State.ENTRY)&valid&(clock<=minute(entry.deadline_et))&(clock<flatten)
        en[enter]=o[enter];ei[enter]=i;stop[enter]=o[enter]-sign[enter]*exit.hard_stop_daily_atr*ad[enter]
        best[enter]=sign[enter]*o[enter];state[enter]=State.IN
        state[(state==State.ENTRY)&~enter]=State.DONE
        close_trade((state==State.IN)&(sign*(o-stop)<=0)&valid,o,i,2)
        adverse=np.where(sign==1,l,h)
        close_trade((state==State.IN)&(sign*(adverse-stop)<=0)&valid,stop,i,3)
        j=(end-570)//5-1 # last COMPLETED 5m feature index
        if j<0:continue
        on5=end%5==0
        atr=feat('atr'+str(exit.atr_minutes),j)
        if fine_exit or on5:
            c=raw['close'][:,i] if fine_exit else cube5['close'][source,j]
            holding=(state==State.IN)&valid
            best[holding]=np.maximum(best[holding],sign[holding]*c[holding])
            why=np.zeros(n,dtype=np.int8)
            elapsed=(i-ei+1)*step
            if exit_signal is not None:
                why[holding&(elapsed>=indicator_exit_grace)&custom(exit_signal,j)]=9
            if exit.failure_minutes:why[holding&(elapsed>=exit.failure_minutes)&(sign*(c-en)<=0)]=7
            if exit.profit_keep:
                bp=best-sign*en
                m=holding&(why==0)&(bp>=exit.profit_activation_daily_atr*ad)&(sign*(c-en)<=exit.profit_keep*bp);why[m]=8
            if exit.structural_minutes:
                level=np.where(sign==1,feat('low'+str(exit.structural_minutes),j)-exit.structural_buffer_atr*atr,feat('high'+str(exit.structural_minutes),j)+exit.structural_buffer_atr*atr)
                why[holding&(why==0)&(sign*(c-level)<=0)]=6
            distance=exit.trailing_atr*(ad if exit.trail_mode=='daily' else atr)
            why[holding&(why==0)&(best-sign*c>=distance)]=4
            why[holding&(why==0)&(elapsed>=exit.max_hold_minutes)]=5
            q=holding&(why>0)&(end<flatten)
            state[q]=State.EXIT;pending[q]=why[q];exit_signal_i[q]=i
        if fine_entry or on5:
            c=raw['close'][:,i] if fine_entry else cube5['close'][source,j]
            entry_adverse=adverse if fine_entry else np.where(sign==1,cube5['low'][source,j],cube5['high'][source,j])
            high=feat('orhigh'+str(entry.opening_minutes),j);low=feat('orlow'+str(entry.opening_minutes),j)
            boundary=np.where(sign==1,high+entry.buffer_daily_atr*ad,low-entry.buffer_daily_atr*ad)
            cond=sign*(c-boundary)>0
            if entry.kind=='reclaim':
                anchor=np.where(sign==1,feat('daily_low',j),feat('daily_high',j))
                breached|=sign*(entry_adverse-anchor)<=-entry.reclaim_depth_atr*atr
                reclaims=np.where(breached&(sign*(c-anchor)>0),reclaims+1,0)
                cond=reclaims>=entry.reclaim_confirmation
            if entry_override is not None:cond=custom(entry_override,j).copy()
            if entry_gate is not None:cond &= custom(entry_gate,j)
            if entry.er_min:cond &= feat('er'+str(entry.er_minutes),j)>=entry.er_min
            if entry.rvol_min:cond &= feat(f'rvol_{entry.rvol_mode}_{entry.rvol_days}',j)>=entry.rvol_min
            if entry.vwap_max_atr:
                dist=sign*(c-feat('vwap',j));cond &= (dist>=0)&(dist<=entry.vwap_max_atr*atr)
                if entry.vwap_confirmation==2:cond &= sign*(feat('prevclose',j)-feat('vwap_prev',j))>0
            if entry.compression_quantile:
                cond &= feat('compressed_'+str(entry.compression_quantile),j)&(feat('range_expansion',j)>=entry.expansion_min)
            if entry.daily_ema:
                cond &= sign*(feat('daily_close',j)-feat('daily_ema'+str(entry.daily_ema),j))>0
                cond &= sign*feat(f'daily_slope{entry.daily_ema}_{entry.daily_slope_days}',j)>0
            if entry.rs_min>-100:cond &= sign*feat('rs'+str(entry.rs_minutes),j)>=entry.rs_min
            allowed=(end>=minute(entry.start_et))&(end<=minute(entry.deadline_et))&(end<flatten)&(end>=570+entry.opening_minutes)
            q=(state==State.WATCH)&cond&allowed&valid
            state[q]=State.ENTRY;signal_i[q]=i
        if trace:hist.append({'minute_close':end,'state':state.tolist(),'entry_index':ei.tolist(),'exit_index':xi.tolist(),'entry_price':np.nan_to_num(en,nan=-1).tolist(),'exit_price':np.nan_to_num(xp,nan=-1).tolist()})
    # No silent last-close liquidation of missing data.
    if ((ei>=0)&(xi<0)).any():raise ValueError('unclosed job / missing flatten bar')
    traded=ei>=0
    out=meta[['date','symbol','direction']].copy()
    out['traded']=traded;out['entry_index']=ei;out['exit_index']=xi
    out['entry_signal_close_minute']=np.where(signal_i>=0,570+(signal_i+1)*step,-1)
    out['entry_minute']=np.where(traded,570+ei*step,-1)
    out['exit_signal_close_minute']=np.where(exit_signal_i>=0,570+(exit_signal_i+1)*step,-1)
    out['exit_minute']=np.where(traded,570+xi*step,-1)
    out['exit_time_exact']=reason!=3
    out['entry_price']=en;out['exit_price']=xp
    out['pnl_points']=np.where(traded,sign*(xp-en),0)
    out['gross_return']=np.where(traded,sign*(xp-en)/en,0)
    out['exit_reason']=[REASONS[int(x)] for x in reason]
    out['eod_flatten']=reason==1;out['unclosed']=False
    out['execution_minutes']=step;out['rule_id']=rule_id(entry,exit)
    if trace:return out,hist
    return out
