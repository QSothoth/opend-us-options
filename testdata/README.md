# OpenD US 0DTE 全托管 — 合成均衡测试集

本目录是**纯合成、固定 seed、可复现**的均衡测试集，用于 OpenD US 末日期权（0DTE）全托管策略的离线开发与状态机回归测试。

> ⚠️ **GitHub 上只有合成数据。** 真实 OpenD / 券商批量行情与成交数据一律只留本地 cache，**禁止提交真实 cache 数据文件**。

## 目录结构

```
testdata/
  README.md                  # 本文件
  manifest.json              # 覆盖与均衡性清单（14 场景 x 2 变体）
  signals/                   # 合成 15m OHLCV，26 根（09:30–16:00 ET）
  events/                    # 期权快照 / 订单事件 JSONL（每行一事件）
  expected/                  # 期望状态机摘要 JSON
  tools/
    build_fixtures.py        # 固定 seed 生成器
    validate_testdata.py     # 分布 + schema 校验器
```

## 快速开始

生成 fixtures：

```bash
cd scripts/opend_us_options/testdata
python3 tools/build_fixtures.py --out .
```

校验（退出码 0 = 通过）：

```bash
python3 tools/validate_testdata.py --out .
```

生成是确定性、纯合成的：`SEED = 42`，不读真实 cache、不连网、不 `--execute`、不下单。

## bars schema（与 cache_store 对齐）

`signals/*.csv` 列固定为：

```
time,open,high,low,close,volume,turnover
```

- `time` = int64 epoch 秒。美东交易时段使用 naive wall-clock 后**当作 UTC** 存（与 cache_store 一致）。
- 15m 一根，一天 26 根：`09:30`–`15:45` ET（最后一根为 `15:45–16:00`）。
- OHLC 满足 `high >= max(open, close)` 且 `low <= min(open, close)`。

## events JSONL

每行一个事件，`type` ∈ `option_snapshot | signal | order_ack | fill | reject | flatten`。
关键字段：`ts, type, symbol, option_code, bid, ask, last, spread, strike, option_type, dte, qty, side, status, scenario_tag`。

## expected JSON schema

```json
{
  "scenario": "strong_up_trend",
  "variant": 1,
  "symbol": "US.SPY",
  "direction": "LONG",
  "should_enter": true,
  "should_exit_reason": "eod",
  "notes": "..."
}
```

- `direction` ∈ `LONG | SHORT | NONE`
- `should_exit_reason` ∈ `eod | stop | target | timeout | reject | none`

## 分布统计表

| # | scenario | variants | direction | should_enter | should_exit_reason |
|---|----------|----------|-----------|--------------|--------------------|
| 1 | strong_up_trend | 2 | LONG | true | eod |
| 2 | strong_down_trend | 2 | SHORT | true | eod |
| 3 | range_chop | 2 | LONG | false | none |
| 4 | gap_up_open | 2 | LONG | true | eod |
| 5 | gap_down_open | 2 | SHORT | true | eod |
| 6 | v_reversal_up | 2 | LONG | true | eod |
| 7 | v_reversal_down | 2 | SHORT | true | eod |
| 8 | late_day_spike | 2 | LONG | true | eod |
| 9 | no_entry_signal | 2 | NONE | false | none |
| 10 | entry_then_eod_flatten | 2 | LONG | true | eod |
| 11 | hard_stop_hit | 2 | SHORT | true | stop |
| 12 | wide_spread_reject | 2 | LONG | false | reject |
| 13 | liquidity_ok_atm_pick | 2 | LONG | true | eod |
| 14 | deadline_no_fill | 2 | LONG | false | timeout |

共 `14 场景 × 2 变体 = 28` 样本；每场景样本数 `2`，`max - min = 0 ≤ 1`，均衡。

## 挂接真实 cache（本地，不提交）

1. 设置环境变量 `OPEND_US_OPTIONS_CACHE_DIR` 指向本地真实行情 cache 目录。
2. 读取时使用**同一 schema**：`time,open,high,low,close,volume,turnover`；`time` 为 int64 epoch 秒（naive ET 当 UTC）。
3. 真实数据仅用于本地回归；**不要 commit / push 任何真实 cache 数据文件**，本目录只保留合成 fixtures。

## 场景分层说明

- **信号/标的侧（1–9）**：以 `signals/*.csv` 为主，`events/*.jsonl` 是同一合成信号下的一致性参考轨迹，`expected/*.json` 为状态机期望摘要。
- **执行/期权侧（10–14）**：以 `events/*.jsonl` 为主，并附一份简短 `signals/*.csv`（复用合成标的形状）作为 underlying 引用。

> **Not an evaluation benchmark.** For real-market metrics use Release `eval-data-v1`.
