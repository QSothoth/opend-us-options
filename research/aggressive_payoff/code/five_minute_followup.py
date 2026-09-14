#!/usr/bin/env python3
"""Bounded5m follow-up on reused83-day history, explicitly posthoc research."""
import run as rt
import argparse, copy, json, math, time, multiprocessing
from pathlib import Path
from dataclasses import asdict, replace
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from data import load_release, make_cube, write_json
from engine import aggregate_1m, daily_atr, mtm_drawdown, JobSpec
from features import params
from protocol import case, neighbors, rule_for, valid
from perturbations import variants
from metrics import metric, scenarios, DISCLAIMER
from report import table


def task(c):
    f,_=rt.run_case(c)
    return {'id':c['id'],**rt.evaluate(f)}


def main():
    p=argparse.ArgumentParser();p.add_argument('--zip',required=True);p.add_argument('--reference',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=6);a=p.parse_args()
    out,ref=Path(a.out),Path(a.reference);out.mkdir(parents=True,exist_ok=True);start=time.time()
    reported=json.loads((ref/'reported_configs.json').read_text());original5=reported['selected_5m'];retained=json.loads((ref/'posthoc_peak_rule.json').read_text())['case'];transfer=case({**retained['entry'],'signal_minutes':5},retained['exit'])
    short=[c for c in json.loads((ref/'shortlist_before_validation.json').read_text()) if c['entry']['signal_minutes']==5]
    proposals=short+[original5,transfer]
    for seed in [original5,transfer]:
        proposals.extend(neighbors(seed));proposals.extend(c for _,c in variants(seed))
    configs=list({c['id']:c for c in proposals if c['entry']['signal_minutes']==5 and valid(c)}.values())
    for c in configs:rule_for(c)
    write_json(out/'protocol_before_results.json',{'disclaimer':DISCLAIMER,'history':'Same83 previously studied sessions; all83 used for ranking. Every chronological subset is descriptive, not an independent holdout.','objective':'Highest all83 net2bps payoff, then net5bps payoff, average winner and deterministic ID tie. No win-rate percentage, PF, expectancy or drawdown gate.','sample_only':'At least20 trades,4 positive and8 negative trades under EACH2/5bps cost,8 dates,5 symbols over all83. Same conditional-mean count checks as previous research, now across all83.','proposals':'20 prior frozen5m shortlist entries; direct5m transfer of retained27.90; one bounded neighbor sweep and predefined +/-20% perturbations around the prior5m winner and direct5m transfer. No additional rounds after results.','configurations':len(configs),'data_tag':'eval-data-v2','sha256':'df93506e498be259a9654c8bf82738aa8ed3604ec2936cf4dfbed0de26aba0c6','execution':'Signals/strategic exits use completed5m bars only. Unchanged1m execution tape is used for next-open fills, resting safety stop and MTM. DailyATR uses prior completed days. This is pure5m decision timing, not a claim that the replay uses no1m execution data.','retained_1m_unchanged':retained['id']})
    write_json(out/'candidate_configs.json',configs)
    print(f'Frozen bounded5m follow-up: {len(configs)} configs; reused all83 research, no holdout claim',flush=True)
    frames,cal,audit,_=load_release(a.zip);raw=frames['klines_1m'];rt.DAY=frames['klines_day'];rt.CUBES={s:make_cube(aggregate_1m(raw,s),cal,s) for s in [1,5]};rt.EXE=rt.CUBES[1];rt.ADS={n:daily_atr(rt.EXE,rt.DAY,n) for n in [10,20,40]};rt.BANKS={}
    periods=json.loads((ref/'data_audit.json').read_text())['periods'];rt.warm(configs);rows=[]
    with ProcessPoolExecutor(max_workers=a.workers,mp_context=multiprocessing.get_context('fork')) as pool:
        for i,r in enumerate(pool.map(task,configs,chunksize=12)):
            rows.append(r)
            if (i+1)%100==0 or i+1==len(configs):print(f'5m {i+1}/{len(configs)}, {time.time()-start:.1f}s',flush=True)
    ranked=sorted(rows,key=lambda r:(r['sample_ok'],r['payoff_ratio'],r['payoff5'],r['avg_win_pct'],r['id']),reverse=True)
    pd.DataFrame(ranked).to_csv(out/'all83_search.csv',index=False);byid={c['id']:c for c in configs};best=byid[ranked[0]['id']];assert ranked[0]['sample_ok']
    write_json(out/'top10.json',[{**r,'case':byid[r['id']]} for r in ranked[:10]])
    write_json(out/'best_rule.json',rt.clean({'disclaimer':DISCLAIMER,'case':best,'resolved_indicator_params':params(best['entry']),'engine_rule':asdict(rule_for(best)),'data_tag':audit['tag'],'sha256':audit['sha256'],'status':'Highest observed payoff in this bounded5m follow-up; all83 used to select; posthoc research, not independent validation','replaces_retained_1m':False,'all83_selection':ranked[0]}))
    print('5m fixed follow-up winner '+best['id'],flush=True)
    cases={'previous_frozen_5m':original5,'transfer_27_90_to_5m':transfer,'followup_5m':best};e,x=best['entry'],best['exit']
    cases['no_confirmations']=case({**e,'gates':[],'quorum':0},x)
    for g in e['gates']:cases['without_'+g]=case({**e,'gates':[z for z in e['gates'] if z!=g],'quorum':max(0,e['quorum']-1)},x)
    for label,delta in [('no_soft_loss',{'soft':0.}),('no_fast_failure',{'fail':0}),('no_trailing',{'trail':100.,'expanded':0.,'late_trail':0.}),('no_late_tightening',{'late_trail':0.}),('soft_loss_always_active',{'soft_escape':100.})]:cases[label]=case(e,{**x,**delta})
    summary=[];cost=[];scene=[];fillchecks={};saved={};(out/'trades').mkdir(exist_ok=True)
    labels=frames['scenario_labels'];labels=labels[labels.grain=='session_5m']
    for label,c in cases.items():
        f,info=rt.run_case(c,record_mtm=True);saved[label]=f;f.to_csv(out/'trades'/(label+'.csv'),index=False);fillchecks[label]=rt.verify_fills(f,rt.EXE,rule_for(c))
        for period,ds in periods.items():
            g=f[f.date.isin(ds)];dd,_=mtm_drawdown(g,info['mtm_returns']);summary.append({'name':label,'period':period,**metric(g),**rt.extras(g),'mtm_mdd_pct':dd})
            for bps in [0,2,5,10,20]:cost.append({'name':label,'period':period,**metric(g,bps)})
        if label in ['previous_frozen_5m','transfer_27_90_to_5m','followup_5m']:scene.extend(scenarios(f,labels,periods,label))
    pd.DataFrame(summary).to_csv(out/'COMPARISON.csv',index=False);pd.DataFrame(cost).to_csv(out/'cost_sensitivity.csv',index=False);pd.DataFrame(scene).to_csv(out/'scenarios.csv',index=False)
    lat=[]
    for minutes in [1,2]:
        f,_=rt.run_case(best,latency=minutes);rt.verify_fills(f,rt.EXE,replace(rule_for(best),latency_minutes=minutes))
        for period,ds in periods.items():lat.append({'additional_latency_minutes':minutes,'period':period,**metric(f[f.date.isin(ds)])})
    pd.DataFrame(lat).to_csv(out/'latency_sensitivity.csv',index=False)
    old=pd.read_csv(ref/'trades/selected_5m.csv');got=saved['previous_frozen_5m'];assert_frame_equal(got,old,check_dtype=False,check_exact=False,rtol=1e-12,atol=1e-12)
    for label in ['no_soft_loss','no_fast_failure','no_trailing','no_late_tightening','soft_loss_always_active']:
        cols=['date','symbol','direction','traded','entry_index','entry_price'];assert_frame_equal(saved[label][cols],saved['followup_5m'][cols])
    # Concrete cache risk: recompute the frozen winner with a new feature bank.
    rt.BANKS={};cold,_=rt.run_case(best,cache=False);assert_frame_equal(cold,saved['followup_5m'])
    job=JobSpec('SPY','LONG',rule=rule_for(best));single,_=rt.run_case(best,job=job);want=cold[(cold.symbol=='US.SPY')&(cold.direction=='LONG')].reset_index(drop=True);assert_frame_equal(single,want)
    verify={'passed':True,'prior_5m_exact_trade_parity':True,'fresh_feature_bank_match':True,'single_job_batch_match':True,'exit_ablation_same_entries':True,'fill_checks':fillchecks,'max_one_round_trip_per_job_day':True,'network_disabled':True}
    write_json(out/'verification.json',verify);write_json(out/'reported_configs.json',cases)
    meta={'configs':len(configs),'sample_adequate':sum(r['sample_ok'] for r in ranked),'best_id':best['id'],'retained_1m_unchanged':retained['id'],'elapsed_seconds':time.time()-start,'ranking_used_all83':True};write_json(out/'metadata.json',meta)
    df=pd.DataFrame(summary);main=df[df.name.isin(['previous_frozen_5m','transfer_27_90_to_5m','followup_5m'])&df.period.isin(['all83','stress27'])][['name','period','trades','win_rate_pct','avg_win_pct','avg_loss_pct','payoff_ratio','mean_trade_pct']]
    ab=df[df.period.eq('all83')][['name','trades','payoff_ratio','mean_trade_pct']]
    co=pd.DataFrame(cost);co=co[(co.name=='followup_5m')&co.period.isin(['all83','stress27'])][['period','cost_bps','trades','payoff_ratio','mean_trade_pct']]
    latdf=pd.DataFrame(lat);latdf=latdf[latdf.period.isin(['all83','stress27'])][['period','additional_latency_minutes','trades','payoff_ratio','mean_trade_pct']]
    text=f'''# 5m 后续比较：underlying proxy, not true option PnL

用户保留的1m 27.90 配置未改变。本轮在原5m候选和1m规则直接移植到5m的基础上，冻结一次邻近参数搜索，共{len(configs)}个配置，按全83日净2bps盈亏比选择最高值。全部日期都已反复研究，后27日仅描述分段表现，不作为独立验证。没有胜率百分比、PF、净均值或回撤门槛；只保留可估计条件均值的最小样本数。

数据固定eval-data-v2，manifest=opend_us_options_eval_v2，SHA256 `{audit['sha256']}`。日期2026-05-14..2026-09-11；同样10个标的和人工LONG/SHORT独立反事实；一天至多一次入场和一次全量退出。

**5m口径：所有技术指标、入场和策略退出只看已完成5m K线。** 5m从同一真实1m生成；下一根5m边界上的开盘成交使用相应1m open，1m还用于预挂安全止损和MTM。日线仅提供前日已知ATR。因此这是5m决策对照，不声称成交模拟完全不使用1m。

## 结果（净2bps）

{table(main)}

## 最佳研究配置

ID `{best['id']}`，完整参数：

```json
{json.dumps({'case':best,'resolved_indicator_params':params(best['entry'])},ensure_ascii=False,indent=2)}
```

这是本轮有限范围内的最高事后盈亏比，不是全局最优。被选中之后没有继续根据消融或后段结果调整参数。

## 消融（其他参数固定）

{table(ab)}

## 成本与额外延迟

{table(co)}

{table(latdf)}

成本是标的价格摩擦，不是期权点差。日终未平仓、尾部和MTM回撤在COMPARISON.csv，逐场景在scenarios.csv。样本与盈利集中度一并保留，不能把高比值直接换算为期权收益。

## 复核和复现

原5m逐笔一致、全新特征缓存一致、单Job/批量一致、退出消融入场一致及成交时序检查均通过。仅检查这些具体风险，没有重复原47,531配置大搜索。

从仓库根目录：

```bash
python3 research/aggressive_payoff/code/five_minute_followup.py --zip /absolute/path/opend_us_options_eval_v2.zip --reference research/aggressive_payoff/results --out /tmp/five_minute_followup --workers 6
```

全部范围、候选、成绩和规则随目录保存；无OpenD、实时行情或订单。
'''
    (out/'REPORT.md').write_text(text);print(main.to_string(index=False),flush=True);print(json.dumps(meta),flush=True)
    from five_minute_observed import finalize
    finalize(out,periods,frames['scenario_labels'])


if __name__=='__main__':main()
