"""Native 1/5/15m signals, common real 1m execution, one round trip per Job/day.
No market/network client. Daily inputs are strictly lagged; intraday resets daily.
"""
from dataclasses import dataclass,asdict
from enum import IntEnum
import hashlib,json,math
import numpy as np
import pandas as pd
from data import SYMBOLS,roll

class State(IntEnum):
    IDLE=0;WATCH=1;ENTRY=2;IN=3;EXIT=4;DONE=5

@dataclass(frozen=True)
class Rule:
    signal_minutes:int=5
    opening_minutes:int=60
    atr_minutes:int=75
    structure_minutes:int=15
    confirmation_minutes:int=15
    entry_start:int=630
    entry_deadline:int=720
    breakout_daily_atr:float=.02
    vwap_max_atr:float=1.5
    structure_buffer_atr:float=.25
    trailing_daily_atr:float=.35
    max_hold_minutes:int=120
    hard_stop_daily_atr:float=.8
    atr_units:str='native'
    latency_minutes:int=0
    structure_activate_daily_atr:float=0.
    expanded_trailing_daily_atr:float=0.
    trail_expand_at_daily_atr:float=.4
    fail_after_minutes:int=0
    fail_required_progress_daily_atr:float=.1
    indicator_exit_min_hold_minutes:int=15
    def __post_init__(self):
        if self.signal_minutes not in (1,5,15):raise ValueError('native signals must be1/5/15m')
        for name in ['opening_minutes','atr_minutes','confirmation_minutes']:
            n=getattr(self,name)
            if type(n)!=int or n<self.signal_minutes or n%self.signal_minutes:raise ValueError('window not representable on native bars: '+name)
        if type(self.structure_minutes)!=int or self.structure_minutes<0 or self.structure_minutes%self.signal_minutes:raise ValueError('invalid structural window')
        if not 570<=self.entry_start<=self.entry_deadline<945:raise ValueError('invalid entry window')
        if type(self.max_hold_minutes)!=int or not 1<=self.max_hold_minutes<=390:raise ValueError('invalid time limit')
        if type(self.latency_minutes)!=int or not 0<=self.latency_minutes<=5:raise ValueError('invalid latency')
        if self.atr_units not in ['native','equivalent_5m_sqrt']:raise ValueError('unknown ATR unit')
        for name in ['breakout_daily_atr','vwap_max_atr','structure_buffer_atr','trailing_daily_atr','hard_stop_daily_atr']:
            if not math.isfinite(getattr(self,name)) or getattr(self,name)<0:raise ValueError(name)
        if min(self.trailing_daily_atr,self.hard_stop_daily_atr)<=0:raise ValueError('positive risk distances required')
        for name in ['structure_activate_daily_atr','expanded_trailing_daily_atr','trail_expand_at_daily_atr','fail_required_progress_daily_atr']:
            if not math.isfinite(getattr(self,name)) or getattr(self,name)<0:raise ValueError(name)
        if self.fail_after_minutes not in (0,15,30,45,60,90):raise ValueError('fail clock')

@dataclass(frozen=True)
class JobSpec:
    symbol:str
    direction:str
    rule:Rule=Rule()
    contract:str|None=None
    max_qty:int=1
    flatten_et:str='15:45'
    dry_run:bool=True
    def __post_init__(self):
        sym='US.'+self.symbol.upper().removeprefix('US.')
        if sym not in SYMBOLS or self.direction not in ['LONG','SHORT']:raise ValueError('human symbol and direction required')
        object.__setattr__(self,'symbol',sym)
        if self.dry_run is not True or type(self.max_qty)!=int or self.max_qty<1:raise ValueError('offline only')
        h,m=map(int,self.flatten_et.split(':'))
        if not 0<=m<=59 or not 630<=h*60+m<=945:raise ValueError('flatten clock')


def config_id(rule,source='common_1m_aggregation'):
    return hashlib.sha256(json.dumps({'rule':asdict(rule),'source':source},sort_keys=True).encode()).hexdigest()[:12]


def aggregate_1m(frame,step):
    """Aggregation of observed REAL bars is not synthetic price generation."""
    if step==1:return frame.copy()
    f=frame.sort_values(['symbol','time']).copy();f['bucket']=((f.time-1)//(step*60)+1)*(step*60)
    g=f.groupby(['symbol','bucket'],sort=True).agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum')).reset_index().rename(columns={'bucket':'time'})
    dt=pd.to_datetime(g.time,unit='s');g['date']=dt.dt.strftime('%Y-%m-%d');g['minute_close']=dt.dt.hour*60+dt.dt.minute
    return g


def daily_atr(cube,day):
    past=[]
    for sym,g in day.groupby('symbol'):
        g=g.sort_values('time');pc=g.close.shift(1)
        tr=pd.concat([g.high-g.low,abs(g.high-pc),abs(g.low-pc)],axis=1).max(axis=1)
        x=g[['date','symbol']].copy();x['daily_atr']=tr.rolling(20,min_periods=20).mean().shift(1).to_numpy();past.append(x)
    merged=cube['keys'][['date','symbol']].merge(pd.concat(past),on=['date','symbol'],how='left',validate='one_to_one')
    if not np.isfinite(merged.daily_atr).all() or not (merged.daily_atr>0).all():raise ValueError('missing prior daily ATR')
    return merged.daily_atr.to_numpy()


def native_features(cube,ad,rule):
    if cube['step']!=rule.signal_minutes:raise ValueError('signal cube/rule mismatch')
    step=rule.signal_minutes;c,h,l,v=(cube[k] for k in ['close','high','low','volume']);valid=np.isfinite(c)
    prev=np.concatenate([cube['open'][:,:1],c[:,:-1]],axis=1)
    tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
    atr=roll(tr,rule.atr_minutes//step)
    if rule.atr_units=='equivalent_5m_sqrt':atr=atr*math.sqrt(5/step)
    vw=np.cumsum(np.nan_to_num((h+l+c)/3*v),axis=1)/np.maximum(np.cumsum(np.nan_to_num(v),axis=1),1)
    vw[~valid]=np.nan
    n=rule.opening_minutes//step
    oh=np.maximum.accumulate(np.where(valid,h,-np.inf),axis=1);ol=np.minimum.accumulate(np.where(valid,l,np.inf),axis=1)
    for a in [oh,ol]:a[:,n:]=a[:,n-1:n];a[:,:n-1]=np.nan
    ns,nt=c.shape;sign=np.array([1.,-1.])[None,:,None]
    boundary=np.stack([oh+rule.breakout_daily_atr*ad[:,None],ol-rule.breakout_daily_atr*ad[:,None]],axis=1)
    dist=sign*(c[:,None,:]-vw[:,None,:])
    gate=(sign*(c[:,None,:]-boundary)>0)&(dist>=0)&(dist<=rule.vwap_max_atr*atr[:,None,:])
    conf=rule.confirmation_minutes//step
    if conf>1:
        for d in range(2):gate[:,d,:]&=roll((dist[:,d,:]>0).astype(float),conf-1,'min',shift=1)>=1
    if rule.structure_minutes:
        n=rule.structure_minutes//step;low=roll(l,n,'min',shift=1);high=roll(h,n,'max',shift=1)
    else:low=np.full_like(c,np.nan);high=low.copy()
    return {'atr':atr,'vwap':vw,'opening_high':oh,'opening_low':ol,'structural_low':low,'structural_high':high,'entry_signal':gate,'daily_atr':ad}


REASONS={0:'no_entry',1:'scheduled_flatten',2:'hard_stop_gap',3:'hard_stop_intrabar',4:'trailing',5:'max_hold',6:'structural',7:'failed_followthrough',8:'indicator_reversal'}


def replay(signals,execution,ad,rule=Rule(),job=None,trace=False,source='common_1m_aggregation',feature_override=None,record_mtm=True):
    """Only native completed-bar closes drive price-based entry/exit.

    1m HLC is used by the fill simulator for an already-resting hard stop and
    descriptive mark-to-market; it never updates strategy indicators/best close.
    Predetermined max-hold/flatten clocks are common across signal frequencies.
    """
    if execution['step']!=1:raise ValueError('fair comparison always uses common1m execution')
    if job:rule=job.rule
    feat=native_features(signals,ad,rule) if feature_override is None else feature_override
    base=signals['keys'];keys=execution['keys']
    ix=base.reset_index().merge(keys[['date','symbol']],on=['date','symbol'],how='right',validate='one_to_one')['index'].to_numpy()
    if not np.isfinite(ix).all():raise ValueError('missing same-date signal source')
    ix=ix.astype(int);repeat=np.repeat(np.arange(len(keys)),2);meta=keys.iloc[repeat].reset_index(drop=True).copy();meta['direction']=np.tile(['LONG','SHORT'],len(keys));src=np.repeat(ix,2);barix=repeat
    if job:
        keep=((meta.symbol==job.symbol)&(meta.direction==job.direction)).to_numpy();meta=meta[keep].reset_index(drop=True);src,barix=src[keep],barix[keep]
    n=len(meta);direction=np.where(meta.direction.to_numpy()=='LONG',0,1);sign=np.where(direction==0,1.,-1.);d_atr=ad[src]
    state=np.full(n,State.IDLE,np.int8);en=np.full(n,np.nan);xp=en.copy();stop=en.copy();best=en.copy()
    ei=np.full(n,-1);xi=ei.copy();entry_signal=ei.copy();exit_signal=ei.copy();ready=ei.copy();pending=np.zeros(n,np.int8);reason=pending.copy()
    mtm=np.zeros((n,390)) if record_mtm else None;hist=[];flatten=meta.flatten.to_numpy().copy()
    if job:
        h,m=map(int,job.flatten_et.split(':'));flatten=np.minimum(flatten,h*60+m)
    raw={k:execution[k][barix] for k in ['open','high','low','close']}
    def close(mask,price,i,why):
        xp[mask]=price[mask] if np.ndim(price) else price;xi[mask]=i;reason[mask]=why[mask] if np.ndim(why) else why;state[mask]=State.DONE
    step=rule.signal_minutes
    for i in range(390):
        clock=570+i;end=clock+1;o,h,l,c=(raw[k][:,i] for k in ['open','high','low','close']);valid=np.isfinite(o)
        active=(state==State.IN)|(state==State.EXIT)
        close(active&(clock>=flatten)&valid,o,i,1)
        # Preserve pending price-exit reason at the same timestamp as hold expiry.
        close((state==State.EXIT)&(clock>=ready)&valid,o,i,pending)
        active=(state==State.IN)|(state==State.EXIT)
        close(active&(clock>=570+ei+rule.max_hold_minutes)&valid,o,i,5)
        enter=(state==State.ENTRY)&(clock>=ready)&(clock<=rule.entry_deadline)&(clock<flatten)&valid
        en[enter]=o[enter];ei[enter]=i;stop[enter]=o[enter]-sign[enter]*rule.hard_stop_daily_atr*d_atr[enter];best[enter]=sign[enter]*o[enter];state[enter]=State.IN
        state[(state==State.ENTRY)&((clock>rule.entry_deadline)|(clock>=flatten))]=State.DONE
        active=(state==State.IN)|(state==State.EXIT)
        close(active&(sign*(o-stop)<=0)&valid,o,i,2)
        active=(state==State.IN)|(state==State.EXIT);adverse=np.where(sign==1,l,h)
        close(active&(sign*(adverse-stop)<=0)&valid,stop,i,3)
        if (end-570)%step==0:
            j=(end-570)//step-1;price=signals['close'][src,j]
            native_valid=np.isfinite(price)&valid
            state[(state==State.IDLE)&(end>=rule.entry_start)&(end<flatten)]=State.WATCH
            holding=(state==State.IN)&native_valid;best[holding]=np.maximum(best[holding],sign[holding]*price[holding])
            why=np.zeros(n,np.int8)
            best_gain=best-sign*en;profit=sign*(price-en);elapsed=end-(570+ei)
            if rule.structure_minutes:
                atr=feat['atr'][src,j];level=np.where(sign==1,feat['structural_low'][src,j]-rule.structure_buffer_atr*atr,feat['structural_high'][src,j]+rule.structure_buffer_atr*atr)
                why[holding&(best_gain>=rule.structure_activate_daily_atr*d_atr)&(sign*(price-level)<=0)]=6
            if rule.fail_after_minutes:
                why[holding&(why==0)&(elapsed>=rule.fail_after_minutes)&(best_gain<rule.fail_required_progress_daily_atr*d_atr)&(profit<=0)]=7
            if 'indicator_exit' in feat:
                why[holding&(why==0)&(elapsed>=rule.indicator_exit_min_hold_minutes)&feat['indicator_exit'][src,direction,j]]=8
            trail=np.full(n,rule.trailing_daily_atr)
            if rule.expanded_trailing_daily_atr:
                trail=np.where(best_gain>=rule.trail_expand_at_daily_atr*d_atr,rule.expanded_trailing_daily_atr,trail)
            why[holding&(why==0)&(best-sign*price>=trail*d_atr)]=4
            why[holding&(why==0)&(end-(570+ei)>=rule.max_hold_minutes)]=5
            q=holding&(why>0)&(end<flatten);state[q]=State.EXIT;pending[q]=why[q];ready[q]=end+rule.latency_minutes;exit_signal[q]=end
            allowed=(end>=rule.entry_start)&(end+rule.latency_minutes<=rule.entry_deadline)&(end+rule.latency_minutes<flatten)
            q=(state==State.WATCH)&feat['entry_signal'][src,direction,j]&allowed&native_valid
            state[q]=State.ENTRY;ready[q]=end+rule.latency_minutes;entry_signal[q]=end
        # An entered job must not be marked DONE just because its flatten quote is missing.
        state[((state==State.IDLE)|(state==State.WATCH)|(state==State.ENTRY))&(end>=flatten)]=State.DONE
        active=(state==State.IN)|(state==State.EXIT);done=(xi>=0)
        if record_mtm:
            mtm[active,i]=sign[active]*(c[active]-en[active])/en[active]-.0001
            mtm[done,i]=sign[done]*(xp[done]-en[done])/en[done]-.0002
        if trace:hist.append({'minute_close':end,'state':state.tolist(),'entry_index':ei.tolist(),'exit_index':xi.tolist(),'entry_price':np.nan_to_num(en,nan=-1).tolist(),'exit_price':np.nan_to_num(xp,nan=-1).tolist()})
    if ((ei>=0)&(xi<0)).any():raise ValueError('unclosed job / missing flatten or exit bar')
    traded=ei>=0;out=meta[['date','symbol','direction']].copy();out['traded']=traded;out['entry_index']=ei;out['exit_index']=xi
    out['entry_signal_close_minute']=entry_signal;out['entry_minute']=np.where(traded,570+ei,-1);out['exit_signal_close_minute']=exit_signal;out['exit_minute']=np.where(traded,570+xi,-1)
    out['exit_time_exact']=reason!=3;out['entry_price']=en;out['exit_price']=xp;out['pnl_points']=np.where(traded,sign*(xp-en),0);out['gross_return']=np.where(traded,sign*(xp-en)/en,0)
    out['exit_reason']=[REASONS[int(r)] for r in reason];out['eod_flatten']=reason==1;out['unclosed']=False;out['signal_minutes']=step;out['execution_minutes']=1;out['rule_id']=config_id(rule,source)
    return out,{'mtm_returns':mtm,'trace':hist,'features':feat}


def mtm_drawdown(frame,mtm):
    worst=0.;by_stream={}
    for key,g in frame.groupby(['symbol','direction'],sort=False):
        g=g.sort_values('date');net=g.gross_return.to_numpy()-g.traded.to_numpy()*.0002
        start=np.r_[1,np.cumprod(1+net)[:-1]]
        path=np.r_[1,(start[:,None]*(1+mtm[g.index])).ravel()]
        if not np.isfinite(path).all():raise ValueError('missing1m mark while holding')
        dd=float(np.min(path/np.maximum.accumulate(path)-1))*100;by_stream['|'.join(key)]=dd;worst=min(worst,dd)
    return worst,by_stream
