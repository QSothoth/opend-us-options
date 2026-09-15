"""Causal, same-session configurable custody timing. No option prices in signals.

Scalar entry/exit functions are shared by the service and optional JIT research
runner. Numeric vector schemas are explicit to keep compiled/Python paths equal.
"""
import copy
import hashlib
import json
import math
from collections import deque
from datetime import date

from .models import Frame, instant
from .timing import IntradayIndicators

FEATURES = ('minute','signed_close','atr','vwap_dist','ema_trend','ema_slope',
 'rsi','adx','di','volume','or_dist','reclaim','roc3','roc10','er20','expansion',
 'clv','body','support5','support10','support20','ema_dist','crosses20',
 'retest','squeeze','gap','count','or_ready','since_break','sweep','donchian5')
ENTRY_KEYS = ('mode','warmup','deadline','relax','early_score','late_score',
 'rsi','adx','volume','distance_cap','er','roc','confirm','retest_depth','or_buffer')
EXIT_KEYS = ('hard','soft','grace','soft_escape','fail','progress','confirm',
 'fail_escape','activation','trail','expand_at','expanded_trail','structure',
 'structure_activation','stall','stall_roc','flatten','min_hold','dte0_clock',
 'fail_mode','dynamic_atr','against_mode')
ENTRY_MODES = ('score','grouped','orb','retest','reclaim','donchian','squeeze',
               'pulse','pullback','adaptive','fixed')
EXIT_REASONS = ('','underlying_safety','soft_failure','failed_followthrough',
                'trailing','structure','stall','scheduled_flatten')
PROFILES = [
 dict(ema_fast=5,ema_slow=13,atr_period=10,rsi_period=7,adx_period=10,volume_window=10,opening_minutes=10),
 dict(ema_fast=9,ema_slow=21,atr_period=14,rsi_period=14,adx_period=14,volume_window=20,opening_minutes=15),
 dict(ema_fast=13,ema_slow=34,atr_period=20,rsi_period=21,adx_period=20,volume_window=40,opening_minutes=30),
 dict(ema_fast=9,ema_slow=21,atr_period=28,rsi_period=14,adx_period=14,volume_window=20,opening_minutes=15),
 dict(ema_fast=5,ema_slow=21,atr_period=14,rsi_period=14,adx_period=14,volume_window=20,opening_minutes=5),
 dict(ema_fast=9,ema_slow=34,atr_period=14,rsi_period=7,adx_period=14,volume_window=40,opening_minutes=15),
]
DEFAULT_ENTRY=[0,15,60,30,4,3,5,18,1.1,0,0,-99,1,.5,0]
DEFAULT_EXIT=[6,0,5,1,20,0,3,0,6,8,0,8,0,2,0,0,375,5,1,0,0,0]


def entry_rule(f,e):
    """Boolean eligibility BEFORE mandatory deadline; no execution fields."""
    if f[0]<e[1] or f[26]<e[1]: return False
    if int(e[0])==10:return True
    vwap=f[3]>0;trend=f[4]>0 and f[5]>0
    rsi=f[6]>=e[6];adx=f[7]>=e[7] and f[8]>0;vol=f[9]>=e[8]
    structure=(f[27]>0 and f[10]>e[14]) or f[11]>0
    mode=int(e[0]);threshold=e[4] if f[0]<e[3] else e[5]
    if mode==0: ok=int(vwap)+int(trend)+int(rsi)+int(adx)+int(vol)+int(structure)>=threshold
    elif mode==1:ok=int(structure)+int(trend or rsi or adx)+int(vol)+int(f[12]>0)>=threshold
    elif mode==2:ok=f[27]>0 and f[10]>e[14] and int(trend)+int(rsi)+int(adx)+int(vol)>=threshold-1
    elif mode==3:ok=f[23]>0 and f[28]<=20 and f[10]>=-e[13] and trend and int(rsi)+int(adx)+int(vol)>=threshold-2
    elif mode==4:ok=f[11]>0 and int(trend)+int(rsi)+int(adx)+int(vol)>=threshold-1
    elif mode==5:ok=f[30]>0 and f[12]>0 and trend and vol and rsi
    elif mode==6:ok=f[24]>0 and f[15]>=1.1 and f[12]>0 and (rsi or adx) and vol
    elif mode==7:ok=f[16]>=.7 and f[17]>=.4 and vol and (trend or rsi)
    elif mode==8:ok=trend and -.25<=f[21]<=e[13] and f[12]>0 and (rsi or vol)
    else:ok=((f[14]>=.35 and structure and trend) or (f[14]<.35 and (f[11]>0 or f[29]>0))) and (rsi or vol)
    if e[9]>0 and f[3]>e[9]:ok=False
    if f[14]<e[10] or f[12]<e[11]:ok=False
    return bool(ok)


def exit_rule(f,x,state,entry_price,entry_minute,atr,dte):
    """Mutates [best signed price, peak minute, contrary count, ratchet].

    Entry price is the *underlying* mark, signed for LONG/SHORT. Returns a reason
    code only; actual option prices and execution delays never enter this rule.
    """
    t=f[0];p=f[1];held=t-entry_minute
    if p>state[0]:state[0]=p;state[1]=t
    gain=(state[0]-entry_price)/atr;profit=(p-entry_price)/atr
    if int(x[21])==0:against=f[3]<0 and f[4]<0
    elif int(x[21])==1:against=f[3]<0 or f[4]<0
    else:against=f[12]<0 and f[5]<0
    state[2]=(state[2]+1 if against else 0) if not f[25] else (1 if against else 0)
    if profit<=-x[0]:return 1
    if x[1]>0 and held>=x[2] and (x[3]<=0 or gain<x[3]) and profit<=-x[1]:return 2
    clock=x[18] if dte==0 else 1.0
    failing=profit<=0 if int(x[19])==0 else profit<x[5]
    if x[4]>0 and held>=x[4]*clock and failing and state[2]>=x[6] and (x[7]<=0 or gain<x[7]):return 3
    width=x[9]
    if x[10]>0 and gain>=x[10]:width=x[11]
    if width>0 and gain>=x[8]:
        scale=max(.5*atr,min(2*atr,f[2])) if x[20]>0 else atr
        line=state[0]-width*scale
        state[3]=max(state[3],line)
        if p<=state[3]:return 4
    if x[12]>0 and held>=x[17] and gain>=x[13]:
        idx=18 if x[12]<=5 else 19 if x[12]<=10 else 20
        if f[idx]<0:return 5
    if x[14]>0 and held>=x[17] and t-state[1]>=x[14]*clock and f[12]<=x[15]:return 6
    return 0


def validate_vectors(e,x):
    if len(e)!=len(ENTRY_KEYS) or len(x)!=len(EXIT_KEYS):raise ValueError('parameter vector length')
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in e+x):raise ValueError('non-finite parameter')
    integer_e=(0,1,2,3,4,5,12);integer_x=(2,4,6,12,14,16,17,19,20,21)
    if any(e[k]!=int(e[k]) for k in integer_e) or any(x[k]!=int(x[k]) for k in integer_x):raise ValueError('fractional discrete parameter')
    if not 0<=e[0]<=10 or not 5<=e[1]<=e[2]<x[16]-1 or not 1<=e[12]<=5:raise ValueError('invalid entry timing')
    if not 0<=e[3]<=e[2] or not 1<=e[4]<=6 or not 1<=e[5]<=6:raise ValueError('invalid score')
    if any(e[k]<0 for k in (7,8,9,10,13,14)) or not 0<=e[10]<=1 or not -50<=e[6]<=50:raise ValueError('invalid entry threshold')
    if any(v<0 for k,v in enumerate(x) if k!=15) or x[0]<=0 or x[18]<=0 or not 300<=x[16]<=375:raise ValueError('invalid exit timing')
    if x[12] not in (0,5,10,20) or x[19] not in (0,1) or x[20] not in (0,1) or x[21] not in (0,1,2):raise ValueError('invalid exit mode')


def make_strategy(profile=1,e=None,x=None,name=None):
    e=list(DEFAULT_ENTRY if e is None else e);x=list(DEFAULT_EXIT if x is None else x)
    validate_vectors(e,x)
    if type(profile) is not int or not 0<=profile<len(PROFILES):raise ValueError('invalid feature profile')
    cfg_entry={**PROFILES[profile], 'signal_minutes':1,'warmup_minutes':int(e[1]),
       'entry_deadline':570+int(e[2]),'relax_at':570+int(e[3]),'score_early':e[4],
       'score_late':e[5],'rsi_distance':e[6],'adx_min':e[7],'volume_ratio_min':e[8]}
    cfg_exit=dict(style='none',structure_minutes=0,expanded=0,stall=0,late_trail=0,
                  hold=390,flatten=570+int(x[16]),safety=x[0],hard_stop_atr=x[0])
    cfg_entry['adaptive']=dict(zip(ENTRY_KEYS,e));cfg_entry['profile']=profile
    cfg_exit['adaptive']=dict(zip(EXIT_KEYS,x))
    rule={'entry':cfg_entry,'exit':cfg_exit}
    cid=hashlib.sha256(json.dumps(rule,sort_keys=True).encode()).hexdigest()[:12]
    doc={'schema_version':1,'case':{**rule,'id':cid},'data_tag':'custody-train-dte4',
         'same_day_only':True,'description':'Payoff-first must-trade candidate; no fixed profit target'}
    raw=(json.dumps(doc,indent=2)+'\n').encode()
    return dict(strategy_id=name or 'custody_adaptive_'+cid,sha256=hashlib.sha256(raw).hexdigest(),
                case_id=cid,config=doc,timing_model='intraday_v2',signal_minutes=1,
                status='candidate_dryrun',true_option_pnl=False,pnl_basis='observed_option_ohlcv_simulation')


class CandidateRegistry:
    def __init__(self,strategy):self.item=copy.deepcopy(strategy)
    def get(self,name):
        if name!=self.item['strategy_id']:raise ValueError('unexpected strategy')
        return copy.deepcopy(self.item)
    def list(self):return [{k:v for k,v in self.item.items() if k!='config'}]


class AdaptiveIndicators:
    def __init__(self,strategy,underlying,direction,session):
        self.base=IntradayIndicators(strategy,underlying,direction,session)
        self.strategy=strategy;self.session=session;self.sign=1 if direction=='LONG' else -1
        self.e=[strategy['config']['case']['entry']['adaptive'][k] for k in ENTRY_KEYS]
        self.rows=deque(maxlen=61);self.crosses=deque(maxlen=20)
        self.prev_vwap=None;self.break_time=-999;self.ready_count=0
        regime=strategy['config']['case']['entry'].get('regime')
        self.regime=None
        if regime is not None:
            from .regime import vectors
            self.regime=vectors(regime)
            self.regime_state=[0.,0.,0.,1000.,1000.]
            self.ready_regime=-1

    def step(self,b):
        t=(instant(b.close_time)-self.session.opens).total_seconds()/60
        if not self.rows and t!=1:raise ValueError('same-day opening prefix missing')
        prev=self.rows[-1] if self.rows else None
        old_vwap=self.prev_vwap
        frame=self.base.step(b);d=frame.diagnostics;a=d['atr_1m'];s=self.sign;p=s*b.close
        vw=d['vwap'];closes=[z.close for z in self.rows]
        reclaim=prev is not None and s*(prev.close-old_vwap)<=0<s*(b.close-vw)
        cross=prev is not None and (prev.close-old_vwap)*(b.close-vw)<0
        self.crosses.append(int(cross));self.prev_vwap=vw
        roc=lambda n:s*(b.close-closes[-n])/a if len(closes)>=n else 0.
        recent=closes[-20:]+[b.close]
        path=sum(abs(y-z) for y,z in zip(recent[1:],recent))
        er=abs(recent[-1]-recent[0])/path if path else 0.
        def sd(v):
            if not v:return 0.
            m=sum(v)/len(v);return (sum((q-m)**2 for q in v)/len(v))**.5
        current_sd=sd((closes+[b.close])[-10:]);old_sd=sd(closes[-20:-10])
        expansion=current_sd/max(old_sd,1e-10) if len(closes)>=20 else 1.
        ran=max(b.high-b.low,1e-10);clv=(b.close-b.low)/ran if s==1 else (b.high-b.close)/ran
        level=self.base.or_high if s==1 else self.base.or_low
        orready=t>self.base.cfg['opening_minutes']
        od=s*(b.close-level)/a
        was_beyond=prev is not None and s*(prev.close-level)>0
        if orready and od>0 and not was_beyond:self.break_time=t
        retest=orready and 0<t-self.break_time<=20 and (b.low<=level+a*.5 if s==1 else b.high>=level-a*.5) and od>=0
        supports=[];sweep=False
        for n in (5,10,20):
            rr=list(self.rows)[-n:]
            support=(min(z.low for z in rr) if s==1 else max(z.high for z in rr)) if rr else b.close
            supports.append(s*(b.close-support)/a)
            if n==5 and rr:sweep=(b.low<support<b.close if s==1 else b.high>support>b.close)
        # Previous range breakout (not merely close above the previous low).
        rr=list(self.rows)[-5:]
        resistance=(max(z.high for z in rr) if s==1 else min(z.low for z in rr)) if rr else b.close
        vector=[t,p,a,s*(b.close-vw)/a,s*(d['ema_fast']-d['ema_slow'])/a,
            s*(self.base.fast-(self.prev_fast if hasattr(self,'prev_fast') else self.base.fast))/a,
            s*(d['rsi']-50),d['rolling_adx'],s*(sum(self.base.plus)-sum(self.base.minus)),
            d['intraday_volume_ratio'],od,int(reclaim),roc(3),roc(10),er,expansion,
            clv,s*(b.close-b.open)/ran,*supports,s*(b.close-d['ema_fast'])/a,
            sum(self.crosses),int(retest),int(expansion>1.1 and old_sd<a),
            int(d['gap_before_bar']),d['observed_bars'],int(orready),t-self.break_time,int(sweep)]
        # Donchian uses the separately exposed signed prior resistance.
        vector.append(s*(b.close-resistance)/a)
        self.prev_fast=self.base.fast;self.rows.append(b)
        extra={};entry_params=self.e
        if self.regime is not None:
            from .regime import route_step,NAMES
            router,entries,_=self.regime
            active=route_step(vector,router,self.regime_state)
            if active!=self.ready_regime:self.ready_count=0
            self.ready_regime=active
            entry_params=entries[active]
            ready=entry_rule(vector,entry_params) and active!=4 and (active!=0 or router[7]>0)
            extra={'regime_id':active,'regime':NAMES[active]}
        else:
            ready=entry_rule(vector,self.e)
        self.ready_count=(self.ready_count+1 if ready else 0) if not vector[25] else int(ready)
        return Frame(frame.symbol,self.strategy['sha256'],frame.bar_close,1,b.close,a,
                     bool(ready and self.ready_count>=entry_params[12]),frame.trend_against,
                     (extra['regime']+':' if extra else '')+ENTRY_MODES[int(entry_params[0])],{'vector':vector,'atr_1m':a,'gap_before_bar':bool(vector[25]),
                     'observed_bars':d['observed_bars'],'vwap':vw,**extra})


def adaptive_exit(job,frame):
    if job['strategy']['config']['case']['entry'].get('regime') is not None:
        from .regime import regime_exit
        return regime_exit(job,frame)
    x=[job['strategy']['config']['case']['exit']['adaptive'][k] for k in EXIT_KEYS]
    f=frame.diagnostics['vector'];s=1 if job['request']['direction']=='LONG' else -1
    opened=instant(job['opens']);entry_minute=(instant(job['entry_at'])-opened).total_seconds()/60
    entry=s*job['entry_underlying'];atr=job['entry_atr']
    state=job.setdefault('adaptive_state',[entry,entry_minute,0.,-1e100])
    dte=(date.fromisoformat(job['contract']['expiry'])-date.fromisoformat(job['request']['trade_date'])).days
    reason=exit_rule(f,x,state,entry,entry_minute,atr,dte)
    return EXIT_REASONS[reason] or None
