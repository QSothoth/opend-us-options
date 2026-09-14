"""Frozen +/-20% local sensitivity, after selection, with no reranking."""
import run as rt
import argparse,json,math,copy,multiprocessing
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import pandas as pd
from data import load_release,make_cube,write_json
from engine import aggregate_1m,daily_atr
from protocol import case,rule_for,valid
from features import params,FAMILY_KEYS
from metrics import metric
PERIODS=None

def variants(base):
    cfg=json.loads((Path(__file__).parent/'post_selection_diagnostics_protocol.json').read_text());out=[('selected',base)];e,x=base['entry'],base['exit'];p=params(e)
    used=set(k for g in e['gates'] for k in FAMILY_KEYS[g])
    if e['trigger']=='impulse':used.update(FAMILY_KEYS['pulse'])
    for name in cfg['entry_parameters']:
        if e[name]>0:
            for factor in cfg['continuous_multipliers']:out.append((f'entry_{name}_x{factor}',case({**e,name:e[name]*factor},x)))
    for name in cfg['indicator_parameters']:
        if name in used and p[name]>0:
            for factor in cfg['continuous_multipliers']:out.append((f'indicator_{name}_x{factor}',case({**e,'indicator_overrides':{**e['indicator_overrides'],name:p[name]*factor}},x)))
    for name in cfg['exit_parameters']:
        if x[name]>0:
            for factor in cfg['continuous_multipliers']:out.append((f'exit_{name}_x{factor}',case(e,{**x,name:x[name]*factor})))
    for name in cfg['timer_minutes_nearest_native_grid']:
        source=e if name=='retest_timeout' else x;value=source[name]
        if value:
            for factor in cfg['continuous_multipliers']:
                adjusted=max(e['signal_minutes'],int(round(value*factor/e['signal_minutes']))*e['signal_minutes']);adjusted=min(adjusted,390 if name=='hold' else 120)
                if name=='retest_timeout':c=case({**e,name:adjusted},x)
                else:c=case(e,{**x,name:adjusted})
                out.append((f'timer_{name}_x{factor}',c))
    for n in cfg['daily_atr_lookbacks']:out.append((f'daily_atr_{n}',case({**e,'daily_atr_days':n},x)))
    result={}
    for label,c in out:
        if valid(c):rule_for(c);result.setdefault(c['id'],(label,c))
    return list(result.values())

def task(item):
    label,c=item;f,_=rt.run_case(c);rows=[]
    for period,ds in PERIODS.items():
        if period not in ['all83','development56','stress27']:continue
        for bps in [2,5]:rows.append({'variation':label,'id':c['id'],'period':period,**metric(f[f.date.isin(ds)],bps)})
    return rows

def main():
    global PERIODS
    p=argparse.ArgumentParser();p.add_argument('--zip',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=6);a=p.parse_args();out=Path(a.out);rec=json.loads((out/'recommended_rule.json').read_text());items=variants(rec['case']);write_json(out/'parameter_perturbation_cases.json',[{'variation':label,**c} for label,c in items])
    frames,cal,_,_=load_release(a.zip);raw=frames['klines_1m'];step=rec['case']['entry']['signal_minutes'];rt.CUBES={s:make_cube(aggregate_1m(raw,s),cal,s) for s in {1,step}};rt.EXE=rt.CUBES[1];rt.DAY=frames['klines_day'];rt.ADS={n:daily_atr(rt.EXE,rt.DAY,n) for n in [10,20,40]};rt.BANKS={};PERIODS=json.loads((out/'data_audit.json').read_text())['periods']
    rt.warm([c for _,c in items]);rows=[]
    with ProcessPoolExecutor(max_workers=a.workers,mp_context=multiprocessing.get_context('fork')) as pool:
        for rr in pool.map(task,items):rows.extend(rr)
    f=pd.DataFrame(rows);f.to_csv(out/'parameter_perturbations.csv',index=False);summ=[]
    for (period,cost),g in f.groupby(['period','cost_bps']):
        summ.append({'period':period,'cost_bps':cost,'configurations':len(g),'min_payoff':g.payoff_ratio.min(),'median_payoff':g.payoff_ratio.median(),'max_payoff':g.payoff_ratio.max(),'min_mean_trade_pct':g.mean_trade_pct.min(),'median_mean_trade_pct':g.mean_trade_pct.median(),'min_trades':int(g.trades.min()),'max_trades':int(g.trades.max())})
    pd.DataFrame(summ).to_csv(out/'parameter_perturbation_summary.csv',index=False);write_json(out/'parameter_perturbation_metadata.json',{'no_reranking':True,'selected_id_unchanged':rec['case']['id'],'unique_variations':len(items),'scope':'Frozen post-selection diagnostic, not a second fitting phase; includes disabled/irrelevant nonzero fields and reports their unchanged outcomes.'})
    concentration=[]
    for label in [f'selected_{step}m','previous_payoff_4_45']:
        source=pd.read_csv(out/'trades'/(label+'.csv'))
        for period,ds in PERIODS.items():
            g=source[source.traded&source.date.isin(ds)].copy();g['net']=g.gross_return-.0002;w=g[g.net>0];wp=w.groupby('date').net.sum();bestday=wp.idxmax() if len(wp) else None;left=g[g.date!=bestday]
            concentration.append({'name':label,'period':period,'trades':len(g),'winning_trades':len(w),'winning_dates':w.date.nunique(),'winning_symbols':w.symbol.nunique(),'largest_profit_date':bestday,'largest_profit_date_share_pct':float(wp.max()/wp.sum()*100) if len(wp) else None,'mean_excluding_best_profit_date_pct':float(left.net.mean()*100) if len(left) else None})
    pd.DataFrame(concentration).to_csv(out/'winning_date_concentration.csv',index=False)
    print(f'Fixed parameter perturbations: {len(items)} configurations, no selection change')

if __name__=='__main__':main()
