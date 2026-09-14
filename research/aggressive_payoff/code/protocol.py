"""Frozen, staged, payoff-only search. No win-rate/PF/expectancy gate."""
import itertools,json,hashlib,copy
from engine import Rule
from features import PACKS,PROFILE_PARAMS,FAMILY_KEYS,params
TRIGGERS=['orb','retest','pullback','donchian','impulse']

def identity(x):return hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()[:12]
def case(e,x):return {'entry':copy.deepcopy(e),'exit':copy.deepcopy(x),'id':identity({'entry':e,'exit':x})}

BASE_EXIT=dict(style='structure',structure_minutes=15,structure_activation=0.,structure_buffer=.03125,trail=.35,expanded=.65,expand_at=.4,hold=390,fail=30,progress=.1,soft=0.,soft_grace=0,soft_escape=.15,stall=0,stall_gain=.1,late_trail=0.,late_start=900,flatten=945,safety=.8)
PROBES=[BASE_EXIT,{**BASE_EXIT,'style':'none','structure_minutes':0,'trail':.5,'expanded':0.,'fail':5,'progress':.1,'soft':.05,'soft_escape':.1}]

def previous_entry(step=1):
    return dict(signal_minutes=step,daily_atr_days=20,trigger='retest',profile=1,opening_minutes=60,warmup_minutes=75,breakout_buffer=.02,retest_depth=.04,retest_timeout=60,donchian_minutes=30,vwap_mode='confirm',confirmation_minutes=15,cap_daily_atr=.1875,entry_deadline=780,gates=['rvol','ema'],quorum=2,indicator_overrides={})

def entry_cases():
    out=[]
    for step,trigger,profile,opening,cap,deadline,pack in itertools.product([1,5,15],TRIGGERS,range(3),[15,30,60],[.125,.1875,.375],[720,840],PACKS):
        e=dict(signal_minutes=step,daily_atr_days=20,trigger=trigger,profile=profile,opening_minutes=opening,warmup_minutes=opening,breakout_buffer=[0.,.02,.04][profile],retest_depth=[.02,.04,.08][profile],retest_timeout=[30,60,90][profile],donchian_minutes=[15,30,60][profile],vwap_mode='confirm' if profile==1 else 'side',confirmation_minutes=15,cap_daily_atr=cap,entry_deadline=deadline,gates=pack[0],quorum=pack[1],indicator_overrides={})
        out.append(e)
    out.append(previous_entry());return list({identity(e):e for e in out}.values())

def exit_cases():
    out=[]
    for style,trail,fail,progress,hold in itertools.product(['none','structure15','structure30_after_gain','ema'],[(.3,0.),(.5,0.),(.35,.8)],[0,5,15,30,60],[.05,.1,.2],[120,240,390]):
        if fail==0 and progress!=.1:continue
        out.append({**BASE_EXIT,'style':'structure' if style.startswith('structure') else style,'structure_minutes':15 if style=='structure15' else 30 if style=='structure30_after_gain' else 0,'structure_activation':.25 if style=='structure30_after_gain' else 0.,'trail':trail[0],'expanded':trail[1],'fail':fail,'progress':progress,'hold':hold})
    return out

def loss_time_variants(c):
    soft=[dict(soft=0.,soft_grace=0,soft_escape=.15)]
    soft += [dict(soft=s,soft_grace=g,soft_escape=e) for s,g,e in itertools.product([.025,.05,.1],[0,5,15],[.1,.25])]
    times=[{},dict(stall=30),dict(stall=60),dict(late_trail=.15),dict(flatten=915),dict(flatten=930)]
    return [case(c['entry'],{**c['exit'],**a,**b}) for a,b in itertools.product(soft,times)]

ENTRY_NEIGHBORS={'opening_minutes':[15,30,45,60,90],'warmup_minutes':[15,30,60,75], 'cap_daily_atr':[.0625,.09375,.125,.1875,.25,.375,.5], 'breakout_buffer':[0.,.01,.02,.04,.08], 'retest_depth':[0.,.02,.04,.08], 'retest_timeout':[15,30,60,90], 'donchian_minutes':[15,30,60], 'vwap_mode':['off','side','confirm'], 'confirmation_minutes':[15,30], 'entry_deadline':[660,720,780,840,900], 'daily_atr_days':[10,20,40],'profile':[0,1,2],'entry_start_override':[0,600,630,645,720]}
INDICATOR_NEIGHBORS={'ema_fast':[15,30,45,60],'ema_slow':[60,90,150,210],'rvol_days':[5,10,20],'rvol_min':[.8,1.,1.25,1.5,2.,2.5,3.,4.], 'adx_minutes':[15,30,60,90],'adx_min':[15,20,30,40], 'rsi_minutes':[15,30,60],'rsi_min':[50,60,70,80], 'er_minutes':[15,30,60],'er_min':[.1,.3,.5,.7], 'roc_minutes':[15,30,60],'roc_min':[0.,.025,.05,.1,.2], 'bb_minutes':[30,60,90],'bb_expansion':[1.,1.1,1.3,1.6], 'squeeze_minutes':[30,60,90],'squeeze_kc':[1.,1.5,2.,2.5],'squeeze_release':[15,30,60], 'super_minutes':[15,30,60],'super_multiple':[1.,1.5,2.,3.,4.], 'pulse_clv':[.6,.75,.9,.95],'pulse_body':[.3,.5,.7,.9],'pulse_range':[.015,.03,.06,.1], 'volume_accel':[1.,1.5,2.,3.,4.], 'rs_minutes':[15,30,60],'rs_min':[0.,.00025,.00075,.0015], 'vwap_slope':[0.,.01,.03,.05],'vwap_cross':[1,2,4,6]}
EXIT_NEIGHBORS={'structure_minutes':[0,15,30,60],'structure_activation':[0.,.1,.25,.5], 'structure_buffer':[0.,.015625,.03125,.0625,.125],'trail':[.15,.25,.35,.5,.8], 'expanded':[0.,.5,.8,1.2], 'expand_at':[.2,.4,.6], 'hold':[60,120,180,240,390], 'fail':[0,5,10,15,30,60], 'progress':[.025,.05,.1,.2,.3], 'soft':[0.,.0125,.025,.05,.1,.2], 'soft_grace':[0,5,15,30], 'soft_escape':[.05,.1,.25,.5], 'stall':[0,15,30,60], 'stall_gain':[0.,.1,.25], 'late_trail':[0.,.1,.2,.35], 'late_start':[870,900,930], 'flatten':[900,915,930,945], 'safety':[.6,.8,1.]}

def valid(c):
    e,x=c['entry'],c['exit'];p=params(e);step=e['signal_minutes']
    if p['ema_fast']>=p['ema_slow']:return False
    if e['entry_deadline']>=x['flatten'] or e.get('entry_start_override',0)>e['entry_deadline']:return False
    if any(p[k]%step for k in ['ema_fast','ema_slow',*[k for k in p if k.endswith('_minutes')],'squeeze_release']):return False
    return True

def neighbors(c):
    out=[c]
    for name,values in ENTRY_NEIGHBORS.items():
        for value in values:out.append(case({**c['entry'],name:value},c['exit']))
    used=set(k for g in c['entry']['gates'] for k in FAMILY_KEYS[g])
    if c['entry']['trigger']=='pullback':used.update(['ema_fast','ema_slow'])
    if c['entry']['trigger']=='impulse':used.update(FAMILY_KEYS['pulse'])
    for name in sorted(used):
        for value in INDICATOR_NEIGHBORS[name]:out.append(case({**c['entry'],'indicator_overrides':{**c['entry']['indicator_overrides'],name:value}},c['exit']))
    for name,values in EXIT_NEIGHBORS.items():
        for value in values:
            x={**c['exit'],name:value}
            if name=='structure_minutes':x['style']='structure' if value else 'none'
            out.append(case(c['entry'],x))
    # Explicit interactions: entry stretch x RVOL, fast failure x close-based loss.
    for cap,rv in itertools.product([.09375,.125,.1875,.25,.375],[1.,1.5,2.,2.5,3.]):
        if 'rvol' in c['entry']['gates']:out.append(case({**c['entry'],'cap_daily_atr':cap,'indicator_overrides':{**c['entry']['indicator_overrides'],'rvol_min':rv}},c['exit']))
    for fail,soft in itertools.product([0,5,10,15,30,60],[0.,.0125,.025,.05,.1,.2]):out.append(case(c['entry'],{**c['exit'],'fail':fail,'soft':soft}))
    return list({x['id']:x for x in out if valid(x)}.values())

def rule_for(c):
    e,x=c['entry'],c['exit'];step=e['signal_minutes']
    return Rule(signal_minutes=step,opening_minutes=e['opening_minutes'],atr_minutes=max(step,e['warmup_minutes']),confirmation_minutes=e['confirmation_minutes'],entry_start=max(570+e['opening_minutes'],e.get('entry_start_override',0)),entry_deadline=e['entry_deadline'],breakout_daily_atr=e['breakout_buffer'],structure_minutes=x['structure_minutes'] if x['style']=='structure' else 0,structure_activate_daily_atr=x['structure_activation'],structure_buffer_atr=x['structure_buffer'],trailing_daily_atr=x['trail'],expanded_trailing_daily_atr=x['expanded'],trail_expand_at_daily_atr=x['expand_at'],max_hold_minutes=x['hold'],fail_after_minutes=x['fail'],fail_required_progress_daily_atr=x['progress'],hard_stop_daily_atr=x['safety'],soft_stop_daily_atr=x['soft'],soft_stop_grace_minutes=x['soft_grace'],soft_stop_until_best_gain_daily_atr=x['soft_escape'],stall_minutes=x['stall'],stall_min_gain_daily_atr=x['stall_gain'],late_trail_daily_atr=x['late_trail'],late_trail_start=x['late_start'],flatten_minute=x['flatten'])

PROTOCOL={
 'version':'aggressive_payoff_83_v1','data_tag':'eval-data-v2','sha256':'df93506e498be259a9654c8bf82738aa8ed3604ec2936cf4dfbed0de26aba0c6','disclaimer':'underlying proxy, not true option PnL',
 'history_exposed':True,'objective':'Maximize robust underlying payoff ratio and average winning size. User defers win-rate gating to a separate future task; no win rate, PF, mean-return, drawdown or scenario gate in selection.',
 'split':'same83days: first28 all fitting; middle28 only frozen shortlist selection; last27 fixed stress. All history already exposed; no pristine OOS claim.',
 'sample_only_gate':'Each28block >=20trades, >=4positive and >=8negative net trades at2bps and >=4positive and>=8negative at5bps, >=8trade dates, >=5symbols. Counts ensure both conditional means are estimable, not a win-rate floor.',
 'score':'log(min(payoff_at2bps,payoff_at5bps)) + .20*log(avg_winner_pct_at2bps/.5). No PF, winrate, expectation or MDD term. Finite clipped floor1e-6 only for numerical safety, not an upper cap.',
 'selection_score':'min(fit_score,validation_score)+.25*mean(fit_score,validation_score)-.10*abs(log(fit_Rrobust/validation_Rrobust)); sample adequacy in both blocks first, then score; deterministic ID ties.',
 'stage_A':'All11340entry grid specifications plus prior4.45 entry, with two fixed exit probes. Per timeframe x trigger retain highest-scoring entry and highest distinct >=2-indicator entry; always carry prior1m entry.',
 'stage_B':'468 exit configurations per retained entry plus probes, on first28 only.',
 'stage_C':'Top2 exits per retained entry x 19soft-loss policies x6late/stall/flatten policies, on first28 only.',
 'stage_D':'Top8 fit candidates per timeframe, max2 per entry; one-at-a-time neighbors of every listed relevant knob plus two explicit joint grids. No middle28 or last27 feedback. Frozen local search is performed once.',
 'shortlist':'Per timeframe top20 across B/C/D, max2 exits per distinct entry; middle28 selects one per timeframe and one overall. Previous4.45 configuration always appears as an additional fixed reference.',
 'entry_trigger':['ORB','ORB retest','EMA pullback','prior Donchian breakout','directional impulse body/close-location/range'],
 'entry_grid':{'native_minutes':[1,5,15],'opening_minutes':[15,30,60],'profiles':PROFILE_PARAMS,'cap_daily_atr':[.125,.1875,.375],'entry_deadline_et':['12:00','14:00'],'confirmation_packs':PACKS},
 'exit_probes':PROBES,'entry_neighbors':ENTRY_NEIGHBORS,'indicator_neighbors':INDICATOR_NEIGHBORS,'exit_neighbors':EXIT_NEIGHBORS,
 'independent_parameters':'Coarse profiles are coupled templates. StageD changes their individual relevant indicator parameters and explicit interactions. This is a bounded staged search, not the exhaustive Cartesian product or a global optimum.',
 'execution':'Only completed native closes can signal. Next real1m open. Soft loss/failure/stall exits are close-based, never backfilled at thresholds. Resting hard stop only uses1m intrabar path, adverse gap at open. Predetermined flatten and maxhold clocks. No profit target.',
 '0dte_adaptation':'Earlier opportunities, prompt invalidation, open-ended winners, optional late tightening/earlier flatten. OHLCV timing hypotheses only; no option theta/IV/gamma data or synthetic precision.',
 'cost_round_trip_bps':2,'cost_stress_bps':[0,2,5,10,20],'latency_stress_minutes':[1,2],
 'scope':'Human specifies symbol and direction; maxone entry andone full exit per day; separate LONG/SHORT counterfactual histories, never a portfolio. No automated security/direction picking. No external confidence gate implemented.',
 'causality':'Prior dailyATR10/20/40 shifted one full day; allnative bars aggregated from same real1m; indicators carry only observed83day RTH history; RVOL prior same-clock sessions(min5); no labels in features or selection.'}
