"""Post-selection ablation and date-cluster intervals; no feedback to fitting."""
import json,math
from pathlib import Path
import numpy as np
import pandas as pd
from data import write_json

def stats(f,dates):
    z=f.assign(net=f.gross_return-f.traded*.0002);z['ws']=z.net.where(z.traded&(z.net>0),0);z['ls']=-z.net.where(z.traded&(z.net<0),0);z['wn']=(z.traded&(z.net>0)).astype(int);z['ln']=(z.traded&(z.net<0)).astype(int)
    return z.groupby('date')[['ws','ls','wn','ln']].sum().reindex(dates,fill_value=0).to_numpy()

def build(out,draws=5000):
    out=Path(out);meta=json.loads((out/'run_metadata.json').read_text());periods=json.loads((out/'data_audit.json').read_text())['periods'];name=f'selected_{meta["best_signal_minutes"]}m';f=pd.read_csv(out/'trades'/(name+'.csv'))
    refs=['previous_payoff_4_45','ablation_no_confirmations','ablation_previous_exit','ablation_no_fast_failure','ablation_no_soft_loss'];frames={k:pd.read_csv(out/'trades'/(k+'.csv')) for k in [name,*refs]};intervals=[];diffs=[];means=[]
    for period in ['all83','development56','stress27']:
        ds=periods[period];rng=np.random.default_rng(20260914);starts=rng.integers(0,len(ds),size=(draws,math.ceil(len(ds)/5)));ix=((starts[:,:,None]+np.arange(5))%len(ds)).reshape(draws,-1)[:,:len(ds)];boots={}
        for k,g in frames.items():
            z=stats(g,ds)[ix].sum(axis=1);r=np.divide(z[:,0]*z[:,3],z[:,1]*z[:,2],out=np.full(draws,np.nan),where=(z[:,1]*z[:,2])>0);boots[k]=r
            intervals.append({'name':k,'period':period,'payoff_ci95_low':float(np.nanquantile(r,.025)),'payoff_ci95_high':float(np.nanquantile(r,.975)),'valid_draws':int(np.isfinite(r).sum()),'draws':draws,'selection_adjusted':False})
        for k in refs:
            diff=boots[name]-boots[k];diffs.append({'selected':name,'baseline':k,'period':period,'payoff_delta_ci95_low':float(np.nanquantile(diff,.025)),'payoff_delta_ci95_high':float(np.nanquantile(diff,.975)),'selection_adjusted':False})
            merged=f.merge(frames[k],on=['date','symbol','direction'],suffixes=('_a','_b'),validate='one_to_one');merged=merged[merged.date.isin(ds)];delta=(merged.gross_return_a-merged.traded_a*.0002)-(merged.gross_return_b-merged.traded_b*.0002);dd=merged.assign(delta=delta).groupby('date').delta.mean().reindex(ds).to_numpy()*10000;boot=dd[ix].mean(axis=1)
            means.append({'selected':name,'baseline':k,'period':period,'delta_mean_bps_per_job':float(dd.mean()),'ci95_low':float(np.quantile(boot,.025)),'ci95_high':float(np.quantile(boot,.975)),'selection_adjusted':False})
    pd.DataFrame(intervals).to_csv(out/'payoff_bootstrap.csv',index=False);pd.DataFrame(diffs).to_csv(out/'payoff_difference_bootstrap.csv',index=False);pd.DataFrame(means).to_csv(out/'paired_mean_bootstrap.csv',index=False)
    reportcases=json.loads((out/'reported_configs.json').read_text());chosen=reportcases[name];ablation=[];parity=[]
    m=pd.read_csv(out/'COMPARISON.csv')
    for other,c in reportcases.items():
        if not other.startswith('ablation_'):continue
        g=pd.read_csv(out/'trades'/(other+'.csv'))
        if c['entry']==chosen['entry']:
            cols=['date','symbol','direction','traded','entry_index','entry_price'];pd.testing.assert_frame_equal(f[cols],g[cols]);parity.append(other)
        for period in periods:
            a=m[(m.name==name)&(m.period==period)].iloc[0];b=m[(m.name==other)&(m.period==period)].iloc[0]
            ablation.append({'ablation':other,'period':period,'trades':int(b.trades),'payoff':b.payoff_ratio,'delta_payoff_vs_selected':b.payoff_ratio-a.payoff_ratio,'mean_trade_pct':b.mean_trade_pct,'delta_mean_trade_pct':b.mean_trade_pct-a.mean_trade_pct,'avg_win_pct':b.avg_win_pct,'avg_loss_pct':b.avg_loss_pct,'mtm_mdd_pct':b.mtm_mdd_pct})
    pd.DataFrame(ablation).to_csv(out/'ABLATION.csv',index=False)
    t=f[f.traded].copy();t['net_return_pct']=(t.gross_return-.0002)*100;t.sort_values('net_return_pct',ascending=False).head(20).to_csv(out/'largest_winners.csv',index=False);t.sort_values('net_return_pct').head(20).to_csv(out/'largest_losses.csv',index=False)
    selected=json.loads((out/'LOCKED_SELECTION_BEFORE_STRESS.json').read_text());rows=[]
    for i,r in enumerate(selected['ranked_shortlist'][:10],1):rows.append({'rank':i,'id':r['id'],'signal_minutes':r['signal_minutes'],'sample_ok':r['sample_ok'],'selection_score':r['selection_score'],'fit_trades':r['fit']['trades'],'fit_payoff':r['fit']['payoff_ratio'],'validation_trades':r['validation']['trades'],'validation_payoff':r['validation']['payoff_ratio'],'entry':json.dumps(r['entry'],sort_keys=True),'exit':json.dumps(r['exit'],sort_keys=True),'stress_used_for_rank':False})
    pd.DataFrame(rows).to_csv(out/'TOP10_DEVELOPMENT_CONFIGURATIONS.csv',index=False)
    write_json(out/'diagnostics_metadata.json',{'seed':20260914,'draws':draws,'block_days':5,'selection_adjusted':False,'same_entry_exit_ablation_parity':parity,'warning':'Repeated-history, post-selection descriptive intervals; not a significance guarantee. No future confidence gate modeled.'})
