# 5m 后续比较：underlying proxy, not true option PnL

用户保留的1m 27.90 配置未改变。本轮在原5m候选和1m规则直接移植到5m的基础上，冻结一次邻近参数搜索，共398个配置，按全83日净2bps盈亏比选择最高值。全部日期都已反复研究，后27日仅描述分段表现，不作为独立验证。没有胜率百分比、PF、净均值或回撤门槛；只保留可估计条件均值的最小样本数。

数据固定eval-data-v2，manifest=opend_us_options_eval_v2，SHA256 `df93506e498be259a9654c8bf82738aa8ed3604ec2936cf4dfbed0de26aba0c6`。日期2026-05-14..2026-09-11；同样10个标的和人工LONG/SHORT独立反事实；一天至多一次入场和一次全量退出。

**5m口径：所有技术指标、入场和策略退出只看已完成5m K线。** 5m从同一真实1m生成；下一根5m边界上的开盘成交使用相应1m open，1m还用于预挂安全止损和MTM。日线仅提供前日已知ATR。因此这是5m决策对照，不声称成交模拟完全不使用1m。

## 结果（净2bps）

| name | period | trades | win_rate_pct | avg_win_pct | avg_loss_pct | payoff_ratio | mean_trade_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| previous_frozen_5m | stress27 | 40 | 10.0000 | 0.2609 | -0.1350 | 1.9324 | -0.0954 |
| previous_frozen_5m | all83 | 103 | 11.6505 | 1.6737 | -0.1396 | 11.9854 | 0.0716 |
| transfer_27_90_to_5m | stress27 | 14 | 7.1429 | 0.4183 | -0.2364 | 1.7699 | -0.1896 |
| transfer_27_90_to_5m | all83 | 59 | 8.4746 | 1.3349 | -0.2023 | 6.5998 | -0.0720 |
| followup_5m | stress27 | 27 | 11.1111 | 0.3912 | -0.1173 | 3.3336 | -0.0608 |
| followup_5m | all83 | 75 | 9.3333 | 2.1980 | -0.1276 | 17.2228 | 0.0894 |

## 最佳研究配置

ID `1ee2ec9d8819`，完整参数：

```json
{
  "case": {
    "entry": {
      "signal_minutes": 5,
      "daily_atr_days": 20,
      "trigger": "retest",
      "profile": 0,
      "opening_minutes": 15,
      "warmup_minutes": 15,
      "breakout_buffer": 0.0,
      "retest_depth": 0.02,
      "retest_timeout": 30,
      "donchian_minutes": 60,
      "vwap_mode": "side",
      "confirmation_minutes": 15,
      "cap_daily_atr": 0.1,
      "entry_deadline": 840,
      "gates": [
        "rvol",
        "adx"
      ],
      "quorum": 2,
      "indicator_overrides": {}
    },
    "exit": {
      "style": "none",
      "structure_minutes": 0,
      "structure_activation": 0.0,
      "structure_buffer": 0.03125,
      "trail": 0.5,
      "expanded": 0.0,
      "expand_at": 0.4,
      "hold": 390,
      "fail": 5,
      "progress": 0.1,
      "soft": 0.1,
      "soft_grace": 5,
      "soft_escape": 0.25,
      "stall": 0,
      "stall_gain": 0.1,
      "late_trail": 0.15,
      "late_start": 900,
      "flatten": 945,
      "safety": 0.8
    },
    "id": "1ee2ec9d8819"
  },
  "resolved_indicator_params": {
    "ema_fast": 15,
    "ema_slow": 45,
    "rvol_days": 10,
    "rvol_min": 1.2,
    "adx_minutes": 15,
    "adx_min": 20,
    "rsi_minutes": 15,
    "rsi_min": 55,
    "er_minutes": 30,
    "er_min": 0.2,
    "roc_minutes": 15,
    "roc_min": 0.025,
    "bb_minutes": 30,
    "bb_expansion": 1.1,
    "squeeze_minutes": 30,
    "squeeze_kc": 1.5,
    "squeeze_release": 15,
    "super_minutes": 30,
    "super_multiple": 1.5,
    "pulse_clv": 0.7,
    "pulse_body": 0.4,
    "pulse_range": 0.025,
    "volume_accel": 1.5,
    "rs_minutes": 15,
    "rs_min": 0,
    "vwap_slope": 0,
    "vwap_cross": 4
  }
}
```

这是本轮有限范围内的最高事后盈亏比，不是全局最优。被选中之后没有继续根据消融或后段结果调整参数。

## 消融（其他参数固定）

| name | trades | payoff_ratio | mean_trade_pct |
| --- | --- | --- | --- |
| previous_frozen_5m | 103 | 11.9854 | 0.0716 |
| transfer_27_90_to_5m | 59 | 6.5998 | -0.0720 |
| followup_5m | 75 | 17.2228 | 0.0894 |
| no_confirmations | 356 | 5.6316 | -0.0371 |
| without_rvol | 281 | 5.7761 | -0.0485 |
| without_adx | 111 | 10.5422 | 0.0604 |
| no_soft_loss | 75 | 16.9007 | 0.0872 |
| no_fast_failure | 75 | 2.3310 | -0.0261 |
| no_trailing | 75 | 18.4477 | 0.0756 |
| no_late_tightening | 75 | 18.4477 | 0.0756 |
| soft_loss_always_active | 75 | 17.2228 | 0.0894 |

## 成本与额外延迟

| period | cost_bps | trades | payoff_ratio | mean_trade_pct |
| --- | --- | --- | --- | --- |
| stress27 | 0 | 27 | 4.2240 | -0.0408 |
| stress27 | 2 | 27 | 3.3336 | -0.0608 |
| stress27 | 5 | 27 | 2.4512 | -0.0908 |
| stress27 | 10 | 27 | 2.5150 | -0.1408 |
| stress27 | 20 | 27 | 1.3047 | -0.2408 |
| all83 | 0 | 75 | 20.6092 | 0.1094 |
| all83 | 2 | 75 | 17.2228 | 0.0894 |
| all83 | 5 | 75 | 13.7544 | 0.0594 |
| all83 | 10 | 75 | 12.0755 | 0.0094 |
| all83 | 20 | 75 | 7.7879 | -0.0906 |

| period | additional_latency_minutes | trades | payoff_ratio | mean_trade_pct |
| --- | --- | --- | --- | --- |
| stress27 | 1 | 27 | 3.8715 | -0.0498 |
| all83 | 1 | 75 | 4.8823 | 0.0045 |
| stress27 | 2 | 27 | 3.3803 | -0.0343 |
| all83 | 2 | 75 | 1.6068 | -0.1313 |

成本是标的价格摩擦，不是期权点差。日终未平仓、尾部和MTM回撤在COMPARISON.csv，逐场景在scenarios.csv。样本与盈利集中度一并保留，不能把高比值直接换算为期权收益。

## 复核和复现

原5m逐笔一致、全新特征缓存一致、单Job/批量一致、退出消融入场一致及成交时序检查均通过。仅检查这些具体风险，没有重复原47,531配置大搜索。

从仓库根目录：

```bash
python3 research/aggressive_payoff/code/five_minute_followup.py --zip /absolute/path/opend_us_options_eval_v2.zip --reference research/aggressive_payoff/results --out /tmp/five_minute_followup --workers 6
```

全部范围、候选、成绩和规则随目录保存；无OpenD、实时行情或订单。

## 消融后的最高观察值

固定398配置网格冠军仍为17.222754；在其后不改其他参数的消融中，`no_late_tightening` 得到更高全期原始盈亏比。按用户“最佳是多少”的要求，另外保留此配置为 `highest_observed_rule.json`（ID `2c0ecc056bd3`），不改写原网格排名，也不继续搜索新参数。

| period | trades | win_rate_pct | avg_win_pct | avg_loss_pct | payoff_ratio | mean_trade_pct | top1_winners_share_pct | top5_winners_share_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fit28 | 23 | 8.6957 | 6.0522 | -0.1274 | 47.5017 | 0.4099 | 63.9470 | 100.0000 |
| validation28 | 25 | 8.0000 | 1.0353 | -0.1584 | 6.5367 | -0.0629 | 81.3946 | 100.0000 |
| development56 | 48 | 8.3333 | 3.5438 | -0.1436 | 24.6775 | 0.1637 | 54.6057 | 100.0000 |
| stress27 | 27 | 7.4074 | 0.4352 | -0.1224 | 3.5547 | -0.0811 | 93.6000 | 100.0000 |
| all83 | 75 | 8.0000 | 2.5076 | -0.1359 | 18.4477 | 0.0756 | 51.4469 | 99.6298 |

与17.22网格冠军相比，最高观察值关闭15:00后的0.15日ATR尾盘收紧，仍保留0.5日ATR普通跟踪。它与完全关闭跟踪的全期汇总结果相同，本轮没有证据证明普通跟踪贡献了增益。高盈亏比伴随更低的每笔净均值；不是所有收益维度同步改善。

这仍是全83日事后比较，仅6笔盈利，最大盈利贡献超过一半的正收益；中间28日和最后27日净均值均为负。完整场景、费用、额外成交延迟及新缓存复核见 `highest_observed_*.csv/json`。

