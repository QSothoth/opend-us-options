"""Append frozen perturbation/cold-replay evidence to the generated report."""
import argparse, json
from pathlib import Path
import pandas as pd
from report import build, table


def finalize(out):
    out=Path(out);build(out);target=out/'REPORT.md';text=target.read_text()
    rec=json.loads((out/'recommended_rule.json').read_text());c=rec['case'];e,x=c['entry'],c['exit'];pp=rec['resolved_indicator_params']
    # Human-readable active defaults, checked against the frozen machine configuration.
    assert e['trigger']=='orb' and e['signal_minutes']==1 and e['gates']==['rvol','rsi'] and e['quorum']==2 and e['vwap_mode']=='side'
    active=f'''## 默认规则怎样执行

**入场：1m 开盘区间突破 + RVOL + RSI，同时满足。** 人工提前指定 symbol 和 LONG/SHORT；程序每天只取第一次合格信号。

| 环节 | 当前真正启用的值 |
| --- | --- |
| 开盘区间 | 09:30–09:45 ET 的高低点；完成的 1m close 向指定方向突破，额外突破缓冲为 0 |
| 信号窗口 | 09:45–12:00 ET；最迟入场 12:00 |
| VWAP | close 位于指定方向一侧，距离不超过 {e['cap_daily_atr']} × 前日已知 ATR20；不要求 15m 确认 |
| RVOL | 至少 {pp['rvol_min']} × 过去 {pp['rvol_days']} 个交易日同一时刻均量，至少 5 个已有交易日 |
| RSI | Wilder RSI，{pp['rsi_minutes']} 根 1m；LONG ≥{pp['rsi_min']}，SHORT ≤{100-pp['rsi_min']}；无超买上限 |
| 初始小亏退出 | 完成的 close 收益 ≤−{x['soft']} × ATR20 就发出退出；最佳已完成 close 浮盈达到 +{x['soft_escape']} × ATR20 后永久解除这项限制 |
| 快速失败退出 | 持有至少 {x['fail']} 分钟、最佳 close 进展仍不足 +{x['progress']} × ATR20，且当前 close 收益 ≤0 时退出 |
| 盈利持有 | 最佳已完成 close 回撤 {x['trail']} × ATR20 才退出；没有固定盈利目标 |
| 安全与时钟 | {x['safety']} × ATR20 硬止损；15:45 ET 强平，已知半日市 12:45；最大持有 390 分钟 |
| 成交 | 信号 close 后下一根真实 1m open；初始小亏值不是保证成交价 |

ATR20 是前 20 个已完成日线 TR 的简单均值，不是当天最终波幅。LONG/SHORT 的价格距离按方向对称。所有主动退出只看已完成 K 线；只有已挂出的硬安全止损使用盘中 high/low。

机器配置保留了统一接口所需字段，但本版本**没有启用 EMA、ADX、Squeeze、Supertrend、结构退出、回踩触发、盈利停滞、尾盘收紧或扩大跟踪距离**。其非零备用参数不代表它们在参与判断。

'''
    text=text.replace('## 选中规则的完整参数\n',active+'## 选中规则的完整参数\n')
    p=pd.read_csv(out/'parameter_perturbations.csv');q=p[~p.variation.str.contains('retest|structure')];summ=[]
    for (period,cost),g in q.groupby(['period','cost_bps']):
        summ.append({'period':period,'cost_bps':cost,'configurations':len(g),'min_payoff':g.payoff_ratio.min(),'median_payoff':g.payoff_ratio.median(),'max_payoff':g.payoff_ratio.max()})
    pd.DataFrame(summ).to_csv(out/'active_parameter_perturbation_summary.csv',index=False)
    concentration=pd.read_csv(out/'winning_date_concentration.csv');concentration=concentration[concentration.name.eq('selected_1m')][['period','winning_trades','winning_dates','winning_symbols','largest_profit_date_share_pct','mean_excluding_best_profit_date_pct']]
    post=pd.read_csv(out/'posthoc_peak_diagnostics.csv');peak=json.loads((out/'posthoc_peak_rule.json').read_text());scores=pd.read_csv(out/'posthoc_peak_development_scores.csv')
    peak_summary=post[(post.variant=='posthoc_peak')&(post.cost_bps==2)&(post.additional_latency==0)][['period','trades','win_rate_pct','avg_win_pct','avg_loss_pct','payoff_ratio','mean_trade_pct']]
    peak_ab=post[(post.period=='all83')&(post.cost_bps==2)&(post.additional_latency==0)][['variant','trades','payoff_ratio','mean_trade_pct']]
    peak_exec=post[(post.variant=='posthoc_peak')&post.period.isin(['all83','stress27'])&(((post.additional_latency==0)&post.cost_bps.isin([2,5,10]))|((post.additional_latency>0)&(post.cost_bps==2)))][['period','cost_bps','additional_latency','trades','payoff_ratio','mean_trade_pct']]
    extra=f'''## 参数扰动与更极端的事后读数

冻结后的 ±20% 单参数扰动及 ATR10/20/40 共 28 个配置，不重新排序默认参数。剔除 6 个实际未启用的回踩/结构字段扰动，余下 22 个（包含原配置）如下；安全网虽没被触发，仍是启用参数，保留在表中。

{table(pd.DataFrame(summ))}

局部范围内差异很大，不能把单点高盈亏比称为参数平台。完整逐配置表见 `parameter_perturbations.csv`。特别是 RSI 和 VWAP 距离改变，会明显改变交易集合；这也说明不能假设后续门禁只删亏损、原样保留大盈利。

**全 83 日事后最高的扰动读数为 27.905：仅把 VWAP 最大距离从 0.125 × ATR20 缩到 0.100 × ATR20。** 它是 ID `{peak['case']['id']}`，规则在 `posthoc_peak_rule.json`。这是观察全部 83 日后按原始盈亏比挑出的研究分支；下表的后 27 日也参与了这个事后识别，不能再称为独立验证。它没有替换冻结默认值，也没有进一步搜索参数。

{table(peak_summary)}

原样计算前两块的样本与得分如下。展示这些事后分块数字不会把它变成预先选出的候选。

{table(scores[['period','trades','wins','losses','payoff_ratio','payoff5','sample_ok','score']])}

这个分支的中间 28 日盈亏比和 5bps 盈亏比都低于冻结默认版。用原来的两块一致性评分回算，它也低于冻结默认版；高全期数字主要来自前段少量大盈利和后段改善。因此交付其可复现规则，但不把“事后最高”改称为“预定标准最优”。它的分场景表另见 `posthoc_peak_scenarios.csv`。

对这个更极端分支也单独做了固定参数消融，避免只交付一个高数字：

{table(peak_ab)}

它的成交压力测试：

{table(peak_exec)}

主消融中还有两项提高全期原始盈亏比：推迟到 10:45 入场为 19.284，但验证段从 10.232 降到 8.560、后段净均值为 −0.0168%；初始小亏规则始终生效为 16.726，前 56 日盈亏比从 16.836 略降到 16.778。它们均保留在交付包，不能只展示有利的全期高值，也不据此回头改变冻结排序。

## 盈利按日期的集中程度

{table(concentration)}

冻结版本前 28 日仅 4 笔盈利、来自 3 个日期；后段的最大盈利日占该段全部正收益的约 40.5%。去掉这一天后，后段每笔均值变负。这里按盈利日期删除全天所有成交，只是影响诊断，不删改原回测。没有对大量试验的赢家偏差进行校正。

'''
    text=text.replace('## 不确定性与审计\n',extra+'## 不确定性与审计\n')
    verification=out/'verification'/'verification.json'
    if verification.exists():
        v=json.loads(verification.read_text());assert v['passed']
        audit=f"\n独立新进程按相反顺序重算 {v['cold_shortlist_candidates']} 个冻结候选的拟合/验证指标和完整排名，全部一致；重放 {v['reported_cases']} 个主报告配置、{v['job_rows_verified']} 个 Job 行及 MTM 指标，误差容限 1e−12。检查使用新的缓存，不重复整个 47,531 配置搜索，见 `verification/verification.json`。\n"
        text=text.replace('## 复现\n',audit+'\n## 复现\n')
    text=text.replace('python3 code/run.py --zip','python3 code/run_all.py --zip')
    text=text.replace('单个人工任务：','主搜索在本环境 6 个进程耗时约 22 分钟，附加诊断和冷复核再需约 2 分钟。仅复核已交付的冻结选择和逐笔结果，无需重复参数搜索：\n\n```bash\npython3 code/verify_frozen.py --zip /absolute/path/opend_us_options_eval_v2.zip --reference results --out verified\n```\n\n单个人工任务：')
    target.write_text(text)
    print('Final report assembled from locked selection, ablations, perturbations and cold verification')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);finalize(p.parse_args().out)
