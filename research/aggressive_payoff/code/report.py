"""Chinese report generated entirely from frozen cases and metrics."""
from pathlib import Path
import json
import pandas as pd

def table(f):
    def fmt(x):
        if pd.isna(x):return '—'
        if isinstance(x,float):return f'{x:.4f}'
        return str(x).replace('|','/')
    return '\n'.join(['| '+' | '.join(f.columns)+' |','| '+' | '.join(['---']*len(f.columns))+' |',*['| '+' | '.join(fmt(x) for x in row)+' |' for row in f.itertuples(index=False,name=None)]])

def build(out):
    out=Path(out);meta=json.loads((out/'run_metadata.json').read_text());rec=json.loads((out/'recommended_rule.json').read_text());data=json.loads((out/'data_audit.json').read_text());df=pd.read_csv(out/'COMPARISON.csv');name=f'selected_{meta["best_signal_minutes"]}m';win=df[(df.name==name)&(df.period=='all83')].iloc[0];stress=df[(df.name==name)&(df.period=='stress27')].iloc[0];old=df[(df.name=='previous_payoff_4_45')&(df.period=='all83')].iloc[0]
    columns=['name','trades','win_rate_pct','avg_win_pct','avg_loss_pct','payoff_ratio','profit_factor','mean_trade_pct','mtm_mdd_pct']
    compare=df[(df.period=='all83')&(df.name.str.startswith('selected_')|df.name.eq('previous_payoff_4_45'))][columns]
    blocks=df[df.name==name][['period',*columns[1:],'cvar05_pct','held_until_flatten_pct','unclosed_after_day_pct']]
    ab=pd.read_csv(out/'ABLATION.csv');ab=ab[ab.period=='all83'][['ablation','trades','payoff','delta_payoff_vs_selected','avg_win_pct','avg_loss_pct','mean_trade_pct']]
    sc=pd.read_csv(out/'scenarios.csv');sc=sc[(sc.rule==name)&(sc.period=='all83')&(sc.direction=='BOTH_COUNTERFACTUAL')][['scenario','trades','win_rate_pct','payoff_ratio','mean_trade_pct','worst_stream_mdd_pct','cvar05_pct','held_until_flatten_pct','unclosed_after_day_pct']]
    co=pd.read_csv(out/'cost_sensitivity.csv');co=co[(co.name==name)&co.period.isin(['all83','stress27'])][['period','cost_bps','trades','payoff_ratio','profit_factor','mean_trade_pct']]
    la=pd.read_csv(out/'latency_sensitivity.csv');la=la[(la.name==name)&la.period.isin(['all83','stress27'])][['period','latency_minutes','trades','payoff_ratio','mean_trade_pct']]
    tail=df[df.name==name][['period','trades','max_win_pct','p90_win_pct','top1_winners_share_pct','top5_winners_share_pct','mean_without_best3_pct','mean_hold_minutes']]
    boot=pd.read_csv(out/'payoff_bootstrap.csv');boot=boot[boot.name==name][['period','payoff_ci95_low','payoff_ci95_high','valid_draws','draws']]
    text=f'''# 激进盈亏比版本：underlying proxy, not true option PnL

**本轮按预定开发期规则选出的版本：{meta['best_signal_minutes']}m，ID `{meta['best_id']}`。** 全 83 日 {int(win.trades)} 笔，扣费后平均盈利 {win.avg_win_pct:.4f}%、平均亏损 {win.avg_loss_pct:.4f}%、盈亏比 **{win.payoff_ratio:.3f}**；上一轮对应盈亏比 {old.payoff_ratio:.3f}。每笔平均净收益 {win.mean_trade_pct:.4f}%，PF {win.profit_factor:.3f}，均如实报告，未作为这轮选参门槛。

最后 27 日固定参数压力段：{int(stress.trades)} 笔，盈亏比 **{stress.payoff_ratio:.3f}**，胜率 {stress.win_rate_pct:.2f}%，每笔净均值 **{stress.mean_trade_pct:.4f}%**。这段不参与本轮排序或改参，且历史已在前面研究中使用过。所谓“最强”仅指这次有限参数范围和预定排序选出的候选，不是全局最优或未来收益保证。

## 本轮目标按你的要求改变

不设胜率、PF、正收益或回撤的选参门槛，不预先假设未来门禁会带来任何改善。主要按 **2bps 与 5bps 两档成本下较低的盈亏比** 排序，并给平均盈利幅度少量权重。样本条件只要求每个 28 日块至少 20 笔、2/5bps 各至少 4 笔盈利和 8 笔亏损、8 个交易日期、5 个标的，以使两类条件均值有基本可估计性；它不是固定胜率门槛。

完整评分：`score = log(min(R_2bps,R_5bps)) + 0.20*log(avg_win_pct_2bps/0.5)`。两段开发期选参用两者较弱得分，加少量均值得分，再扣除盈亏比差异项。没有额外隐藏的净收益或胜率评分。

若盈亏比很高而胜率很低，净均值仍可能为负。这里没有把未来门禁当成已经验证的改善，也没有通过改标签、删除亏损日期或选择方向来提高结果。

## 同源数据与本轮适配

数据固定为 [eval-data-v2](https://github.com/QSothoth/opend-us-options/releases/tag/eval-data-v2)，manifest=`opend_us_options_eval_v2`，ZIP SHA256：
`{meta['sha256']}`。

最近共同 83 个交易日为 2026-05-14 至 2026-09-11，SPY/QQQ/IWM/AAPL/NVDA/TSLA/MU/AMD/META/MSFT 固定不筛选。1m 真实 K 线统一聚合 5m 和 15m，成交统一使用下一根真实 1m open；本轮各周期独立调参，因此不是仅改变周期的因果实验。

针对当天到期场景，测试了更早的 15/30 分钟开盘区间、Donchian 突破、强实体/收盘位置/波幅冲量、较快 EMA/RSI/ADX、ROC 加速、RVOL 与成交量加速、BB/KC Squeeze、Supertrend 样式 ATR 趋势带。退出侧尝试 5/10/15 分钟快速失败检查、初始小亏退出、盈利后解除初始小亏限制、宽跟踪、结构退出延后、盈利停滞退出、尾盘收紧和提前强平。

这些都是“及时走出方向并保留大幅价格运动”的标的择时假设。没有期权分钟价格、IV、Greeks 或真实点差，无法估计 theta/gamma 的实际贡献，也不构造看似精确的期权收益。

## 搜索规模与时间切分

- 前 28 日：{data['periods']['fit28'][0]} 至 {data['periods']['fit28'][-1]}，所有入场、退出、局部细化仅在此拟合。
- 中间 28 日：{data['periods']['validation28'][0]} 至 {data['periods']['validation28'][-1]}，只选定预先冻结的 {meta['validation_candidates']} 个候选。
- 后 27 日：{data['periods']['stress27'][0]} 至 {data['periods']['stress27'][-1]}，只检查冻结配置。
- 分阶段评估次数：{json.dumps(meta['trials'])}；不重复拟合配置 **{meta['unique_fit_configs']}** 个。两段均满足样本条件的候选 {meta['sample_adequate_validation_candidates']} 个。
- 初始参数是粗网格模板，随后分别扫描相关参数，并联合扫描“VWAP 偏离×RVOL”“失败分钟×初始亏损退出距离”。不是所有旋钮的全笛卡尔积；全部范围、阶段保留规则和每次成绩在包内。

## 全 83 日对照

{table(compare)}

收益为标的代理，单位为百分比。LONG/SHORT 是独立的人工方向反事实，汇总表没有构造 20 仓位账户。每个 symbol+direction+date 最多一次入场与一次全量退出；可以不交易、不再入场、不加仓、不分批卖出。

## 选中规则的完整参数

`entry` 表示入场/确认指标；`exit` 表示失败检查与盈利持有规则。技术指标 profile 的实际值已展开，机器调用不必从报告猜阈值。

```json
{json.dumps({'case':rec['case'],'resolved_indicator_params':rec['resolved_indicator_params']},ensure_ascii=False,indent=2)}
```

价格距离均使用前日已知的日 ATR；日 ATR 长度由 `daily_atr_days` 指定，在完整日线 TR 上先滚动再 shift1。结构缓冲 `structure_buffer` 直接为日 ATR 倍数。`soft` 是初始阶段的收盘判定亏损界限，达到 `soft_escape` 最佳已完成 close 浮盈后解除；它不是挂在该价位的盘中止损。软退出、失败退出和趋势退出都等下一根 1m open，不能用盘中碰价伪造成交。

`progress` 是失败检查所需的最佳 close 进展，只有持有超过 `fail` 分钟、最佳进展不足且当前 close 收益≤0 才退出；不在达到 progress 时止盈。`expanded/expand_at` 是盈利后放宽跟踪，`stall` 检查多久没创已完成 close 新高，`late_trail` 在 `late_start` 后收紧跟踪；0 表示该功能关闭。`flatten` 用 ET 午夜起分钟数表示，915/930/945 分别为15:15/15:30/15:45。任何情况下都不跨日，无固定盈利目标。

## 时间稳定性与风险

{table(blocks)}

MTM 回撤取各 symbol+direction 独立 1x 收益流中最差者，包含实际持有期间的 1m close 浮动，未乘期权杠杆。CVaR5 是最差 5% 交易平均收益；成本固定往返 2bps。`held_until_flatten_pct` 为持有至预设强平的交易比例，`unclosed_after_day_pct` 才是跨日未平仓。

## 消融：其他参数固定，不重新调优

{table(ab)}

确认指标的删除定义为把被删条件固定为通过：AND 少一个条件；三选二删一个后变为剩余二者至少一个通过，保证是放宽该条件，不偷偷收紧其他门槛。`previous_exit` 使用同一批入场换回上一轮退出；同入场的所有退出消融均做逐笔入场一致性检查。开启时参数为0的模块消融可能完全相同，这种情况不能称为有效贡献。

## 大盈利与集中程度

{table(tail)}

Top1/Top5 为最大 1/5 笔盈利占所有正收益之和，去掉最佳 3 笔仅作为脆弱性诊断，不作为选参门槛。

## 分场景

{table(sc)}

场景标签事后才可知，只作报告分层，未进入特征、评分或选择；标签可重叠、不能把各行相加。缺少盈利或亏损样本的盈亏比留空；小样本标签的高盈亏比不能当成稳定优势。完整文件包含所有周期、时间块及方向。

## 成本与延迟

{table(co)}

{table(la)}

2/5/10/20bps 是标的价格摩擦压力，不等同于期权实际点差。交易的正负归属会随成本变化，因此条件均值之比可能非单调；净收益不会因增加成本而变好。延迟可改变是否在截止时间前成交，表中同时报告交易数。

## 不确定性与审计

{table(boot)}

使用 5 日区块、5000 次重采样，日期内保留全部标的与方向，随机种子 20260914。区间是重复历史上的描述性估计，没有校正大量参数选优。配对盈亏比差和每任务净收益差也随包提供。

前版本 103 笔交易兼容、未来价格/成交量/当前日线投毒、全部 39 个指标 profile 条件、选中周期的状态机前缀、单 Job 与批量结果、软退出实际收盘条件、下一分钟开盘成交、强平缺失失败均做了检查，见 `audit_tests.json`。评测脚本禁止 TCP/IP 和 DNS，不含 OpenD、行情接口或订单客户端。

## 复现

Python 3.12 + numpy + pandas，POSIX 环境（Linux/macOS；Windows 可用 WSL）。数据需自行准备同 SHA 的固定 ZIP；代码包不包含真实大数据。

```bash
python3 code/run.py --zip /absolute/path/opend_us_options_eval_v2.zip --out reproduced --workers 6
```

单个人工任务：

```bash
python3 code/replay_job.py --zip /absolute/path/opend_us_options_eval_v2.zip --rule results/recommended_rule.json --symbol SPY --direction LONG --out spy_long
```

代码/参数/搜索记录/逐 Job 结果/消融/场景/成本/延迟/置信区间一起交付。未来胜率门禁没有实现，也没有把其潜在改善预先计入这份结果。
'''
    (out/'REPORT.md').write_text(text)

if __name__=='__main__':
    import sys
    build(sys.argv[1])
