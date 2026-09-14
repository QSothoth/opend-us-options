"""Preserve the highest observed5m ablation without rewriting the frozen grid ranking."""
import run as rt
import argparse,json
from pathlib import Path
from dataclasses import asdict,replace
import pandas as pd
from pandas.testing import assert_frame_equal
from data import load_release,make_cube,write_json
from engine import aggregate_1m,daily_atr,mtm_drawdown
from protocol import rule_for
from features import params
from metrics import metric,scenarios,DISCLAIMER
from report import table


def finalize(out,periods,labels):
    out=Path(out);configs=json.loads((out/'reported_configs.json').read_text());original=json.loads((out/'best_rule.json').read_text());base=original['case'];observed=[]
    for name,c in configs.items():
        f=pd.read_csv(out/'trades'/(name+'.csv'));m=rt.evaluate(f)
        changes=sum(c[section][k]!=base[section][k] for section in ['entry','exit'] for k in base[section])
        observed.append((m['sample_ok'],m['payoff_ratio'],m['payoff5'],-changes,name,c))
    _,_,_,_,name,best=max(observed,key=lambda row:row[:5]);assert next(r for r in observed if r[4]==name)[0]
    rec={'disclaimer':DISCLAIMER,'case':best,'resolved_indicator_params':params(best['entry']),'engine_rule':asdict(rule_for(best)),'data_tag':original['data_tag'],'sha256':original['sha256'],'status':'Highest observed net2bps payoff among frozen398-grid winner and subsequent fixed-parameter ablations; selection used all83; not independently validated','source_ablation':name,'frozen_grid_winner_unchanged':base['id'],'replaces_retained_1m':False}
    write_json(out/'highest_observed_rule.json',rec)
    rt.BANKS={};f,info=rt.run_case(best,record_mtm=True,cache=False);expected=pd.read_csv(out/'trades'/(name+'.csv'));assert_frame_equal(f,expected,check_dtype=False,check_exact=False,rtol=1e-12,atol=1e-12);rt.verify_fills(f,rt.EXE,rule_for(best))
    rows=[]
    for period,ds in periods.items():
        g=f[f.date.isin(ds)];dd,_=mtm_drawdown(g,info['mtm_returns'])
        for cost in [0,2,5,10,20]:rows.append({'period':period,'additional_latency_minutes':0,**metric(g,cost),**rt.extras(g),'mtm_mdd_pct_at2bps':dd})
    labels=labels[labels.grain=='session_5m'];pd.DataFrame(scenarios(f,labels,periods,'highest_observed_5m')).to_csv(out/'highest_observed_scenarios.csv',index=False)
    for minutes in [1,2]:
        g,_=rt.run_case(best,latency=minutes);rt.verify_fills(g,rt.EXE,replace(rule_for(best),latency_minutes=minutes))
        for period,ds in periods.items():rows.append({'period':period,'additional_latency_minutes':minutes,**metric(g[g.date.isin(ds)])})
    summary=pd.DataFrame(rows);summary.to_csv(out/'highest_observed_diagnostics.csv',index=False)
    write_json(out/'highest_observed_verification.json',{'passed':True,'fresh_bank_matches_original_ablation':True,'native5m_signals_and_next_open_fills':True,'source_ablation':name,'case_id':best['id'],'new_parameters_searched':False,'selection_used_all83':True})
    m=summary[(summary.cost_bps==2)&(summary.additional_latency_minutes==0)];overall=m[m.period=='all83'].iloc[0];winning_count=round(overall.trades*overall.win_rate_pct/100)
    section=f'''## 消融后的最高观察值

固定398配置网格冠军仍为17.222754；在其后不改其他参数的消融中，`{name}` 得到更高全期原始盈亏比。按用户“最佳是多少”的要求，另外保留此配置为 `highest_observed_rule.json`（ID `{best['id']}`），不改写原网格排名，也不继续搜索新参数。

{table(m[['period','trades','win_rate_pct','avg_win_pct','avg_loss_pct','payoff_ratio','mean_trade_pct','top1_winners_share_pct','top5_winners_share_pct']])}

与17.22网格冠军相比，最高观察值关闭15:00后的0.15日ATR尾盘收紧，仍保留0.5日ATR普通跟踪。它与完全关闭跟踪的全期汇总结果相同，本轮没有证据证明普通跟踪贡献了增益。高盈亏比伴随更低的每笔净均值；不是所有收益维度同步改善。

这仍是全83日事后比较，仅{winning_count}笔盈利，最大盈利贡献超过一半的正收益；中间28日和最后27日净均值均为负。完整场景、费用、额外成交延迟及新缓存复核见 `highest_observed_*.csv/json`。

'''
    target=out/'REPORT.md';text=target.read_text();marker='## 消融后的最高观察值\n'
    if marker in text:text=text.split(marker)[0]
    target.write_text(text+'\n'+section)
    print('Highest observed5m '+best['id']+' from '+name,flush=True);print(m[['period','trades','payoff_ratio','mean_trade_pct']].to_string(index=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--zip',required=True);p.add_argument('--reference',required=True);p.add_argument('--out',required=True);a=p.parse_args();out=Path(a.out)
    frames,cal,_,_=load_release(a.zip);rt.DAY=frames['klines_day'];rt.CUBES={s:make_cube(aggregate_1m(frames['klines_1m'],s),cal,s) for s in [1,5]};rt.EXE=rt.CUBES[1];rt.ADS={n:daily_atr(rt.EXE,rt.DAY,n) for n in [10,20,40]};rt.BANKS={}
    periods=json.loads((Path(a.reference)/'data_audit.json').read_text())['periods'];finalize(out,periods,frames['scenario_labels'])
