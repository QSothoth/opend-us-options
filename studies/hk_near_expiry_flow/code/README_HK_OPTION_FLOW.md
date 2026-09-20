# 港股正股期权近月扫描（OpenD，只读）

用富途 OpenD 扫 **港股上市正股期权** 的近月成交。默认数据源是日频合约排行 `get_option_rank`（含已到期合约，适合回测）。可选 `--source event` 看盘中异动成交，但 **过期近月合约会被清掉**，长区间回放会变空。

不扫涡轮，不碰美股期权，不下单。

Python：`/home/box/futu/venv/bin/python3`  
脚本：`/home/box/futu/option_flow/scan_hk_option_flow.py`  
OpenD：仅 `OpenQuoteContext(host='127.0.0.1', port=11111)`。

## 两个数据源（必读）

| 来源 | API | 适合 | 历史近月 |
|---|---|---|---|
| **`rank`（默认）** | `get_option_rank` | 日频扫描 / 回测 | 本机约 **2026-08-07 → 最近一个港股交易日**。已到期、当日成交过的合约仍在排行里。 |
| `event` | `get_option_event` | 盘中异动逐笔 | **只保留仍在上市的合约**。过期近月会被清掉，一年回测会看起来没信号。 |

`get_option_rank` 的 `trading_date='YYYY-MM-DD'` **必须校验返回的 `trading_date` 列**。非交易日或过旧日期，OpenD 可能静默返回 **最新一天** 的排行（代码、成交额都和今天一样）。脚本会丢掉这些天。

**不要用 `LEFT_DAYS` 做历史过滤**：它相对 **今天** 算，不是相对 `trading_date`。到期日一律从合约代码 `YYMMDD`（正则 `(\d{6})[CPcp]`）相对 **排行日** 计算。

频率大约 60 次 / 30 秒；翻页或按日循环会自动 sleep / 退避。

## 用法

```bash
cd /home/box/futu/option_flow

# 盘中 / 最近一个交易日：近月认购排行表
/home/box/futu/venv/bin/python3 scan_hk_option_flow.py --source rank --side CALL

# 回测（默认 rank）：aggressive 预设
/home/box/futu/venv/bin/python3 scan_hk_option_flow.py --backtest --source rank --preset aggressive

# 指定区间（只会留下校验通过的交易日）
/home/box/futu/venv/bin/python3 scan_hk_option_flow.py --backtest --source rank --preset pi \
    --from 2026-08-07 --to 2026-09-18

# 只看 MiniMax
/home/box/futu/venv/bin/python3 scan_hk_option_flow.py --backtest --source rank \
    --owners HK.00100 --from 2026-09-16 --to 2026-09-16

# 旧的盘中异动逐笔（历史近月不完整）
/home/box/futu/venv/bin/python3 scan_hk_option_flow.py --source event \
    --min-turnover 200000 --max-dte 5 --side CALL --action BUY
/home/box/futu/venv/bin/python3 scan_hk_option_flow.py --backtest --source event --preset pi
```

## 默认参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--source` | `rank` | `rank` / `event` |
| `--side` | `CALL` | `CALL` / `PUT` / `ALL` |
| `--action` | `BUY` | 仅 event：`BUY` / `SELL` / `NEUTRAL` / `ALL` |
| `--min-turnover` | `200000` 港元 | rank：单合约当日成交额；event：单笔成交额 |
| `--min-owner-turnover` | `500000` 港元 | **仅 rank**：同一标的、同一天、近月同方向成交额合计。`0` 关闭 |
| `--min-volume` | `0` | 单合约成交量 |
| `--min-oi-increment` | 关 | **仅 rank**：要求增仓量 ≥ N |
| `--max-dte` | `5` | 相对排行日（rank）或成交日（event）的到期天数，区间 `[0, max_dte]` |
| `--rank-type` | `TURNOVER` | `TURNOVER` / `VOLUME` / `OI` / `OI_INCREMENT` |
| `--rank-top` | `400` | 每日最多翻页条数（每页最多 200） |
| `--count` | `200` | rank 上限 200；event 上限 300 |
| `--pages` | live 1 / rank 回测 2 / event 回测 5 | 翻页上限 |
| `--from` / `--to` | 最早已验证历史日 → 最近港股交易日 | rank 区间 |
| `--since` / `--until` | 空 | event 成交日过滤；rank 下也可当 `--from`/`--to` 的别名 |
| `--sleep` | `0.55` 秒 | 两次 OpenD 调用的最小间隔 |
| `--min-underlying-chg` | 关 | rank：排行日收盘相对昨收；event：成交时标的价相对昨收。单独写 flag = 0（要求翻红） |
| `--preset` | 关 | `default` / `conservative` / `aggressive` / `pi`。命令行显式参数覆盖预设 |

event 仍保留 `--cluster-*`、`--cluster-only`、`--max-day-num`、`--min-buy-sell-ratio`、`--require-stock-not-up`。

## 预设（概念保留，rank 上重新映射）

这些是 **检测** 工作点，不是交易系统。rank 下信号是 **标的日**（同一正股 + 同一交易日的近月成交），不再是 event 的逐笔扎堆。

| 预设 | rank 含义 | 大致等价 |
|---|---|---|
| `default` | 到期 ≤5 日，单合约 ≥20 万，标的日近月合计 ≥50 万 | `--max-dte 5 --min-turnover 200000 --min-owner-turnover 500000` |
| `conservative` | 更严成交额，到期 ≤7 日，且 **当日收盘相对昨收 ≥ 0** | `--max-dte 7 --min-turnover 500000 --min-owner-turnover 1500000 --min-underlying-chg 0` |
| `aggressive` | 更松成交额，到期 ≤14 日 | `--max-dte 14 --min-turnover 200000 --min-owner-turnover 1000000` |
| `pi` | 与 aggressive 相同的成交形态，再加上 **排行日收盘已翻红**（只用当日 K 的昨收/收盘，不看次日） | aggressive + `--min-underlying-chg 0` |

event 下四个名字仍是原来的逐笔扎堆映射（见历史 threshold / pi 报告）。`--source event --backtest` 会打印历史近月不完整的警告。

## rank 报警规则

同时满足：

1. `option_type` 符合 `--side`（默认认购）
2. 用合约代码 `YYMMDD` 相对 **排行日** 算出的到期天数 ∈ `[0, --max-dte]`
3. 该标的日近月里，成交额最大的合约 ≥ `--min-turnover`（以及可选的量、增仓）
4. 该标的日近月同方向成交额合计 ≥ `--min-owner-turnover`（若 > 0）
5. 若开了 `--min-underlying-chg N`：排行日收盘相对昨收 ≥ N%（收盘可知，不偷看次日）
6. 若开了 `--require-stock-not-up N`：排行日涨幅不超过 N%

正股代码优先用 `get_stock_basicinfo` 的 `stock_owner`，否则用合约根代码静态表（`MNX` → `HK.00100` 等）。

## 输出

- 盘中 rank：`<out-dir>/latest_hk_option_flow.csv`
- 回测 rank：`hk_option_rank_raw.csv`、`hk_option_alerts.csv`、`hk_option_daily_rollup.csv`
- 回测 event：`hk_option_events_raw.csv`、`hk_option_alerts.csv`、`hk_option_daily_rollup.csv`

回测还会打 MiniMax / `HK.00100` / `MNX260918C235` 在 2026-09-16 的检查段。

## 不做

- 涡轮全市场扫描
- 美股 0DTE
- 编造约 2026-08-07 之前的排行历史
- 交易：不用 `OpenSecTradeContext`，不解锁，不 `place_order`
