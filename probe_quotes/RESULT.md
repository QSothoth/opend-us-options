# 美股期权行情能力探测 RESULT

生成时间：2026-09-12 15:59:43（本机时区）
探测脚本：`probe_option_quotes.py`（可复跑）；原始数据：`probe_raw.json`，完整日志见脚本 stdout。
连接：OpenD 127.0.0.1:11111；SDK futu-api 10.10.7008；标的：`US.AAPL`

> 仅读行情，未下任何订单。

## 0. 权限 / 账户

- `qot_logined=True`，`trd_logined=True`，`server_ver=1010`
- 美股行情权限 `us_qot_right` = **LV3**
- 美股期权行情权限 `us_option_qot_right` = **LV1**
- 订阅额度 `sub_quota` = 300；历史K线额度 `history_kl_quota` = 300
- 订阅后 `option_used_quota=4 / option_remain_quota=56`（期权订阅总上限=60）

## A. 现在能不能拿到期权实时/快照行情？有哪些字段？

**能。** 快照 `get_market_snapshot([option_code])`、实时报价 `get_stock_quote`、逐笔 `get_rt_ticker`、摆盘 `get_order_book`、期权专用 `get_option_quote` 全部 ret=0。

探测合约：`US.AAPL260914C332500`（到期 2026-09-14，ATM CALL，strike=332.5）

| 来源 | 字段 | 值 |
| --- | --- | --- |
| `last_price` | `1.95` |
| `bid_price` | `1.88` |
| `ask_price` | `2.05` |
| `bid_vol` | `87.0` |
| `ask_vol` | `1.0` |
| `volume` | `21143.0` |
| `turnover` | `6097215.0` |
| `option_open_interest` | `1009` |
| `option_implied_volatility` | `19.818` |
| `option_delta` | `0.491208059` |
| `option_gamma` | `0.078010472` |
| `option_vega` | `0.102894487` |
| `option_theta` | `-0.549970565` |
| `option_rho` | `0.009720531` |
| `option_premium` | `0.656` |
| `option_strike_price` | `332.5` |
| `strike_time` | `2026-09-14` |
| `option_expiry_date_distance` | `2` |
| `option_type` | `CALL` |
| `update_time` | `2026-09-11 15:59:57` |

`get_option_quote({code})` 额外给出（更丰富）：

| 字段 | 值 |
| --- | --- |
| `price` | `1.95` |
| `mid_price` | `1.9649999999999999` |
| `mark_price` | `1.965` |
| `implied_volatility` | `19.818` |
| `delta` | `0.491208059` |
| `gamma` | `0.078010472` |
| `vega` | `0.102894487` |
| `theta` | `-0.549970565` |
| `rho` | `0.009720531` |
| `intrinsic_value` | `0.0` |
| `time_value` | `1.95` |
| `breakeven_point` | `[334.465]` |
| `dist_to_breakeven` | `[-2.194999999999993]` |
| `prob_of_profit` | `33.11312393888417` |
| `seller_roi` | `0.5949047092838436` |
| `leverage_ratio` | `169.094` |
| `effective_gearing` | `83.06` |
| `days_to_expiry` | `2` |

- 快照字段列举（列名）：`code, name, update_time, last_price, open_price, high_price, low_price, prev_close_price, volume, turnover, turnover_rate, suspension, listing_date, lot_size, price_spread, stock_owner, ask_price, bid_price, ask_vol, bid_vol, enable_margin, mortgage_ratio, long_margin_initial_ratio, enable_short_sell, short_sell_rate, short_available_volume, short_margin_initial_ratio, amplitude, avg_price, bid_ask_ratio, volume_ratio, highest52weeks_price, lowest52weeks_price, highest_history_price, lowest_history_price, close_price_5min, after_volume, after_turnover, sec_status, equity_valid`
- 实时报价（订阅 QUOTE 后 `get_stock_quote`）字段：last/open/high/low/volume/turnover/IV(bid/ask 走快照)、greeks、open_interest、premium 等。
- 逐笔 `get_rt_ticker`：20 行，列 `['code', 'name', 'time', 'price', 'volume', 'turnover', 'ticker_direction', 'sequence', 'type']`。
- 摆盘 `get_order_book`：{'code': 'US.AAPL260914C332500', 'name': 'AAPL 260914 332.50C', 'svr_recv_time_bid': '', 'svr_recv_time_ask': '', 'order_book_type': 'NORMAL', 'Bid': [(1.88, 87.0, 1, {})], 'Ask': [(2.05, 1.0, 1, {})]}

**注意**：实测非流动性/深实值合约的 IV 与 greeks 可能返回 `0`（如 260918 C240000），平值活跃合约（C325000~C340000）IV≈25~27、delta/gamma/theta/vega 均正常。

## B. 能不能拿历史日K/分钟K做回测？粒度/多久？

**能。** `request_history_kline` 对期权 code 直接返回 ret=0（返回三元组 `ret, df, page_req_key`，分页 key 用于翻页）。

- K_DAY: 成功，首屏 2 根；最早 2026-09-10 00:00:00，最晚 2026-09-11 00:00:00；翻页=False
- K_1M: 成功，首屏 780 根；最早 2026-09-10 09:31:00，最晚 2026-09-11 16:00:00；翻页=False
- K_5M: 成功，首屏 156 根；最早 2026-09-10 09:35:00，最晚 2026-09-11 16:00:00；翻页=False

### 不同到期日合约的历史跨度对比（关键：历史长短取决于挂牌时间）

| 类型 | 合约 | 到期日 | 日K根数 | 日K起点 | 日K终点 | 1M首屏 | 1M起点 | 1M终点 | 1M可翻页 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| nearest | `US.AAPL260914C332500` | 2026-09-14 | 2 | 2026-09-10 00:00:00 | 2026-09-11 00:00:00 | 780 | 2026-09-10 09:31:00 | 2026-09-11 16:00:00 | False |
| ~30d | `US.AAPL261009C330000` | 2026-10-09 | 11 | 2026-08-27 00:00:00 | 2026-09-11 00:00:00 | 1000 | 2026-08-27 09:31:00 | 2026-08-31 13:10:00 | True |
| ~90d | `US.AAPL261218C330000` | 2026-12-18 | 618 | 2024-03-26 00:00:00 | 2026-09-11 00:00:00 | 1000 | 2024-03-26 09:31:00 | 2024-03-28 13:10:00 | True |
| farthest | `US.AAPL290119C330000` | 2029-01-19 | - | empty | - | - | empty | - | - |

- 日K：期权上市首日 → 最近交易日，字段 open/close/high/low/volume/turnover/change_rate/last_close。
  实测同一 AAPL 标的、不同到期日差异极大：**新挂周合约仅 2 天**历史（260914），**月度/远期合约可达 600+ 个交易日 / 2 年+**（261218 自 2024-03-26 起 618 根）；**并非所有挂牌合约都有 K 线**（最远 LEAPS 290119 返回空，未成交/新挂）。
- 分钟K：支持 `K_1M/K_5M/K_15M/K_30M/K_60M` 等。单页最多 1000 根，用 `page_req_key` 翻页，可从合约**上市首日**开始拉取。活跃平值合约 1 分钟约 300~470 根/交易日，长历史合约全生命周期约 10 万+ 根，翻页即可全部取回（历史短于 1000 根时一次性返回、无 page key）。
- 实时分钟K：`get_cur_kline(code, n, K_1M)` **必须先 `subscribe(SubType.K_1M)`**，订阅后 ret=0 返回最近 n 根；未订阅返回原文：`Before calling the Get Real-time Candlestick interface, please subscribe to KL_1Min data first.`

### 回测粒度/跨度结论

- 粒度：**日K + 1/5/15/30/60 分钟K** 都可用；做「分钟级回测」没问题。
- 跨度：**逐合约**从上市日到摘牌/到期。实测 AAPL 不同到期日差异很大：新挂周合约仅几天，远期月合约可回溯 2 年+（618 个交易日）。所以做多年回测要自行把同一标的的多个月份/多个到期日合约拼接成连续序列，并对「上市初期成交稀、无成交分钟」做清洗。
- 额度：`history_kl_quota` 初始 300；本次拉取了 3 个不同到期日的 AAPL 期权日K+1M 后，`used/remain` 仍为 8/292（`before` / `after` 均为 `(8, 292, ...)`），说明额度不是按请求次数计，而是按「标的（期权按标的+到期日）」计的滚动窗口额度，翻页/重复拉取同一标的不会额外扣减。

## C. 失败原因 / 限制

1. **退订时长限制**：订阅（QUOTE/K_1M 等）后必须持有 **≥ 1 分钟** 才能 `unsubscribe`，否则原文：`The Basic subscription duration for ... is too short. Minimum subscription duration is 1 minute.`（本脚本完整模式会等 61s 再退订，已验证可成功退订：{'ret': 0, 'msg': 'None'}）
2. **实时分钟K必须先订阅 K_1M**：否则 `get_cur_kline` ret=-1（原文见 B 节）。
3. **期权订阅额度按 (合约, 类型) 计**：订阅 1 个合约 × 4 种类型（QUOTE/TICKER/ORDER_BOOK/K_1M）即占用 4 个额度，`option_used_quota=4 / option_remain_quota=56`，总上限 60。即最多约 15 个合约 × 4 类（或 60 个合约 × 1 类），需要做 LRU/批量轮换。
4. **IV/greeks 对不活跃合约可能为 0**：优先用平值近月合约。
5. **`get_option_chain` 单次时间窗 ≤ 30 天**，需按到期日分段查询。
6. **`request_history_kline` 返回三元组**，直接 `ret, df = ...` 会报 `too many values to unpack`。
7. 本次探测**未出现权限不足**：`us_option_qot_right=LV1` 已足够拿到快照/实时/历史/分钟K；未触发限频（所有请求均返回 ret=0，仅退订时长限制和未订阅提示）。
8. **历史 K 线并非所有合约都有**：新挂/无成交合约（如 LEAPS 290119）`request_history_kline` 返回成功但空表。
9. 本次探测在**美股盘后**进行，样例 `update_time` 为最近收盘前后的最后成交/报价，无法区分「实时 vs 延迟」；`us_option_qot_right=LV1`，正式盘中建议再确认一次数据时延。

## D. 「先回测、后实盘盯分钟K」可行性结论

**可行。** 当前账户（美股期权 LV1）已经可以逐合约拉取从上市日到最近的日K与 1/5 分钟K（`request_history_kline` + `page_req_key` 翻页），回测阶段无需任何额外权限，注意每日 300 的历史额度消耗和同一合约跨月份拼接；实盘盯盘阶段可用 `subscribe(QUOTE/K_1M)` 拿实时报价与实时分钟K（`get_cur_kline`）外加 `get_market_snapshot` 取 IV/greeks，但受期权订阅额度上限约束，需要控制同时订阅的合约数并遵守 1 分钟最短订阅时长；整体链路已跑通，唯一需要额外设计的是「合约池轮换订阅」和「跨月合约拼接」两件事。

---

### 附：原始关键数据

```json
{
  "expiry": "2026-09-14",
  "atm_call": {
    "code": "US.AAPL260914C332500",
    "strike": 332.5
  },
  "atm_put": {
    "code": "US.AAPL260914P332500",
    "strike": 332.5
  },
  "history": {
    "K_DAY": {
      "ret": 0,
      "n": 2,
      "columns": [
        "code",
        "name",
        "time_key",
        "open",
        "close",
        "high",
        "low",
        "pe_ratio",
        "turnover_rate",
        "volume",
        "turnover",
        "change_rate",
        "last_close"
      ],
      "earliest": "2026-09-10 00:00:00",
      "latest": "2026-09-11 00:00:00",
      "has_next_page": false
    },
    "K_1M": {
      "ret": 0,
      "n": 780,
      "columns": [
        "code",
        "name",
        "time_key",
        "open",
        "close",
        "high",
        "low",
        "pe_ratio",
        "turnover_rate",
        "volume",
        "turnover",
        "change_rate",
        "last_close"
      ],
      "earliest": "2026-09-10 09:31:00",
      "latest": "2026-09-11 16:00:00",
      "has_next_page": false
    },
    "K_5M": {
      "ret": 0,
      "n": 156,
      "columns": [
        "code",
        "name",
        "time_key",
        "open",
        "close",
        "high",
        "low",
        "pe_ratio",
        "turnover_rate",
        "volume",
        "turnover",
        "change_rate",
        "last_close"
      ],
      "earliest": "2026-09-10 09:35:00",
      "latest": "2026-09-11 16:00:00",
      "has_next_page": false
    }
  },
  "history_span": [
    {
      "label": "nearest",
      "expiry": "2026-09-14",
      "code": "US.AAPL260914C332500",
      "day_n": 2,
      "day_from": "2026-09-10 00:00:00",
      "day_to": "2026-09-11 00:00:00",
      "min1_n": 780,
      "min1_from": "2026-09-10 09:31:00",
      "min1_to": "2026-09-11 16:00:00",
      "min1_paged": false
    },
    {
      "label": "~30d",
      "expiry": "2026-10-09",
      "code": "US.AAPL261009C330000",
      "day_n": 11,
      "day_from": "2026-08-27 00:00:00",
      "day_to": "2026-09-11 00:00:00",
      "min1_n": 1000,
      "min1_from": "2026-08-27 09:31:00",
      "min1_to": "2026-08-31 13:10:00",
      "min1_paged": true
    },
    {
      "label": "~90d",
      "expiry": "2026-12-18",
      "code": "US.AAPL261218C330000",
      "day_n": 618,
      "day_from": "2024-03-26 00:00:00",
      "day_to": "2026-09-11 00:00:00",
      "min1_n": 1000,
      "min1_from": "2024-03-26 09:31:00",
      "min1_to": "2024-03-28 13:10:00",
      "min1_paged": true
    },
    {
      "label": "farthest",
      "expiry": "2029-01-19",
      "code": "US.AAPL290119C330000",
      "day_err": "empty",
      "min1_err": "empty"
    }
  ],
  "history_kl_quota_before": [
    8,
    292,
    []
  ],
  "history_kl_quota_after": "(8, 292, [{'code': 'US.MU', 'name': 'Micron Technology', 'request_time': '2026-09-08 22:52:12'}, {'code': 'US.SNDK', 'name': 'SanDisk', 'request_time': '2026-09-08 00:32:21'}, {'code': 'US.SNXX', 'name': 'Tradr 2X Long SNDK Daily ETF', 'request_time': '2026-09-08 00:32:21'}, {'code': 'US.SKHY', 'name': 'SK hynix', 'request_time': '2026-09-08 13:20:42'}, {'code': 'US.SKDD', 'name': 'GraniteShares 2X Short SK Hynix Daily ETF', 'request_time': '2026-09-08 13:20:54'}, {'code': 'US.SKHZ', 'name': 'Leverage Shares 1X Short SK Hynix Daily ETF', 'request_time': '2026-09-08 13:20:54'}, {'code': 'US.SKHN', 'name': 'Tradr 2X Short SK hynix Daily ETF', 'request_time': '2026-09-08 13:20:54'}, {'code': 'US.SKHQ', 'name': 'Leverage Shares 2x Short SK Hynix Daily ETF', 'request_time': '2026-09-08 13:20:54'}, {'code': 'US.AAPL', 'name': 'Apple [opt exp 2026-09-14]', 'request_time': '2026-09-12 23:58:29'}, {'code': 'US.AAPL', 'name': 'Apple [opt exp 2026-09-18]', 'request_time': '2026-09-12 23:49:24'}, {'code': 'US.AAPL', 'name': 'Apple [opt exp 2026-10-09]', 'request_time': '2026-09-12 23:56:43'}, {'code': 'US.AAPL', 'name': 'Apple [opt exp 2026-10-16]', 'request_time': '2026-09-12 23:53:11'}, {'code': 'US.AAPL', 'name': 'Apple [opt exp 2026-12-18]', 'request_time': '2026-09-12 23:56:45'}, {'code': 'US.AAPL', 'name': 'Apple [opt exp 2029-01-19]', 'request_time': '2026-09-12 23:56:48'}])",
  "subscription_after": {
    "total_used": 0,
    "remain": 300,
    "option_used_quota": 4,
    "option_remain_quota": 56,
    "own_used": 0,
    "own_security_firm": "N/A",
    "own_option_used_quota": 4,
    "sub_list": {
      "QUOTE": [
        "US.AAPL260914C332500"
      ],
      "ORDER_BOOK": [
        "US.AAPL260914C332500"
      ],
      "TICKER": [
        "US.AAPL260914C332500"
      ],
      "K_1M": [
        "US.AAPL260914C332500"
      ]
    }
  },
  "unsubscribe": {
    "ret": 0,
    "msg": "None"
  }
}
```