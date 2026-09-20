# 数据需求：港股期权流量信号（给在另一台机器上补数据的人）

写于 2026-09-20。背景：R1～R3 共 13 个候选全部不达标，见
[reports/R1_R3_RESULTS.md](reports/R1_R3_RESULTS.md)。**卡住的是数据，不是想法**，
本文列出要补什么、怎么存、补多少算够。

只读行情，不涉及下单：全程只用 `OpenQuoteContext`，不要 `OpenSecTradeContext`、
不要 `unlock_trade`、不要 `place_order`。

## 0. 优先级

| 级别 | 数据 | 没有它会怎样 |
|---|---|---|
| **P0** | 每日 rank 快照，**CALL + PUT** | 做不了认购/认沽失衡——最标准的流量信号现在完全缺失 |
| **P0** | 每日异动逐笔 `get_option_event` | rank 不带方向。没有它，「买入」只能从价格冲击间接猜 |
| **P0** | 全部 owner 的正股日 K + 指数基准 | 现在 78 个近月 owner 里只有 40 个能算收益，母体白丢 1/3 |
| **P1** | 近月合约收盘买卖价差快照 | 港股期权价差很宽，不记价差，任何「能赚多少」都是假的 |
| **P1** | 报警合约的后续期权日 K | 产品是买期权，最终必须按期权成交价算盈亏 |
| P2 | 正股 1m | 将来做日内择时才用，现在不急 |

**所有 P0/P1 都必须当天收盘后拉当天的。** rank 历史约 6 周后会静默串到最新一天，
异动接口会把已到期合约整条清掉——**漏一天就永久补不回来**，这是唯一真正的硬约束。

---

## 1. P0-A 每日期权排行快照

`OpenQuoteContext.get_option_rank(market=OptionMarket.HK_SECURITY, ...)`，
每个港股交易日收盘后（建议 16:30 HKT 之后）拉当天。

**维度：** `option_type ∈ {CALL, PUT}` × `rank_type ∈ {TURNOVER, VOLUME, OI_INCREMENT}`，
每个组合翻页到 **1000 条**（每页上限 200），六份结果按 `code` **取并集去重**。

现在只拉了 CALL × TURNOVER × 400 条，近月行中位数 54 行/天——小票的近月合约系统性看不到。

**三个必须遵守的坑（现有数据就是被这几条坑过的）：**

1. **必须校验返回的 `trading_date` 列 == 请求日期。** 非交易日或过旧日期，OpenD 会
   静默返回**最新一天**的排行，代码和成交额和今天一模一样。不等就整天作废并记进 manifest。
2. **不要用服务端 `LEFT_DAYS` 过滤。** 它相对**今天**算，不是相对 `trading_date`。
   到期日一律从合约代码的 `YYMMDD`（正则 `(\d{6})[CPcp]`）相对**排行日**自己算。
3. **owner 在拉取时就要落盘。** 用 `get_stock_basicinfo` 的 `stock_owner`
   （或期权静态信息）拿正股代码，写进 `owner` 列；**不要**靠根代码静态表——现在有
   24 个根代码没映射出来（`AIA` `HEX` `JDC` `YOF` `WXB` `SHK` `BEA` `INB` `WXA`
   `WHG` `HES` `CKH` `SBO` `COS` `CHQ` `ZAO` `MTR` `AMC` `SET` `BOM` `NFU` `CCE`
   `TRF` `BLI`），这些标的日直接算不了收益。

**必需列**（原样保存，不要裁剪、不要过滤、不要改名）：

```
code, name, option_type, volume, turnover, open_interest,
oi_increment, oi_decrement, iv, option_price, change_ratio,
delta, gamma, theta, vega, rho, trading_date
```

外加拉取时自己算的两列：`dte`（相对排行日）、`owner`（`HK.xxxxx`）。

`iv / delta / theta / change_ratio / option_price` 是**必需**的，不是可选：目前唯一有量级的
特征（delta+theta 残差，rho=+0.159）全靠这几列。缺任何一列这条线就断了。

## 2. P0-B 每日异动逐笔（唯一带方向的数据）

`get_option_event(market=OptionMarket.HK_SECURITY)`，每天收盘后拉**当天**，
`max_day_num=1`，**不要**按 action 过滤（`BUY` / `SELL` / `NEUTRAL` 全要），
CALL / PUT 全要，翻页到底（每页上限 300）。

必需列：`fill_time, option_code, price, volume, turnover, action, underlying_price`
（接口给什么存什么，宁可多存）。

这是整件事里最关键的一块：rank 只告诉你「成交了多少」，混着做市、对冲、平仓；
`action` 是唯一能区分主动买卖的字段。**它只保留仍在上市的合约，不当天冻就没了。**

## 3. P0-C 正股日 K + 指数基准

`get_history_kline(..., ktype=K_DAY)`，必需列：
`code, time_key, open, high, low, close, volume, turnover, last_close`。

范围：

- 当天 rank / event 里出现过的**全部 owner**（不是只有大票）。
- 加两个基准：**`HK.800000`（恒生指数）**、**`HK.800700`（恒生科技）**。
  现在的大盘因子是拿 54 只期权标的的横截面均值凑的，有了指数就能做得干净。
- 每天滚动拉最近 **30 个交易日**即可（要覆盖信号日前 10 天做基线、后 5 天算 T+1～T+3）。

现在具体缺的（有 HK 代码但没日 K，按近月出现次数排）：
`HK.01347`(67) `HK.09888`(20) `HK.02628`(19) `HK.00175`(6) `HK.09660` `HK.00003`
`HK.01288` `HK.06030` `HK.01772` `HK.06862` `HK.02020` `HK.02015` `HK.01339` `HK.01336`。

## 4. P1-D 近月合约收盘价差快照

收盘后对当天 **DTE ≤ 14 的全部合约**（CALL+PUT，约 100～250 个代码）跑一次
`get_market_snapshot`，存 `code, update_time, bid_price, bid_vol, ask_price, ask_vol,
last_price, open_interest`。

rank 里的 `bid_price` / `ask_price` 基本是 `N/A`。港股近月期权价差动辄百分之几到十几，
**不记价差，任何"这个信号能赚 0.5%"的说法都不成立**——那点超额可能还不够跨过一个价差。

## 5. P1-E 报警合约的后续期权日 K

对当天 DTE ≤ 14 且成交额 ≥ 20 万港元的合约，**从那天起到它到期为止**，每天拉一次该
合约的日 K（`code, time_key, open, high, low, close, volume, turnover`）。

理由：本项目的盈亏只按期权成交价算。rank 只有当合约还排在前 N 名时才有价格，
掉出榜就没了——直接拿 rank 当价格序列会有幸存者偏差。

---

## 6. 落盘格式

一天一个目录，不覆盖、不回填、不做任何过滤：

```text
hk_flow/<YYYY-MM-DD>/
  rank.csv            P0-A 六个维度的并集
  event.csv           P0-B 当天全部异动逐笔
  kline_day.csv       P0-C 全部 owner + 两个指数，最近 30 个交易日
  snapshot.csv        P1-D 近月合约收盘买卖盘
  option_kline.csv    P1-E 跟踪中合约的日 K
  manifest.json       拉取时间(UTC+8)、OpenD 版本、每个源的行数、
                      trading_date 校验结果、翻页次数、重试/限频次数、失败项
  CHECKSUMS.sha256    每个文件一行 "<sha256>  <相对路径>"
```

频率限制约 60 次 / 30 秒，翻页和按日循环之间 sleep ≥ 0.55 秒并做退避。
被限频要记进 manifest，不要静默吞掉。

**验收标准（必须能过）：** 把这批数据按现有列名拼成
`hk_option_rank_raw.csv` + `klines_day.csv` 两个文件放进一个目录，然后

```bash
python3 code/flow_research.py --dataset <该目录> --side CALL
python3 code/flow_research.py --dataset <该目录> --side PUT
```

两条都能跑出 `BASE` 行，且打印的 `days` 等于实际交易日数。跑不通说明列名或
`dte` / `owner` 没对齐，回来改采集，不要在研究端打补丁。

---

## 7. 要补多少才够

T+1 大盘调整后超额收益的标准差实测 **3.22%**（n=381）。按双侧 α=0.05、功效 80%：

| 想检出的超额 | 需要的信号数 |
|---:|---:|
| 0.3% | ≈ 906 |
| **0.5%** | **≈ 326** |
| 0.8% | ≈ 127 |
| 1.0% | ≈ 82 |

现在每天中位 17 个近月 CALL 标的，其中只有 12 个能算收益。补齐日 K、加上 PUT、
翻页加深之后，估计每天 25～35 个可用标的日；一条触发率 20% 的规则约 5～7 个信号/天。

→ **要检出 0.5% 量级的效应，需要大约 50～70 个交易日的完整采集；
要在多个市场阶段上站得住，按 3～6 个月准备。**

但比天数更硬的约束是**阶段**：现在这 30 天是单一下跌段（面板累计 −2.25%）。
至少要覆盖**一段上涨和一段下跌**，否则再多天数也只是把同一个阶段的噪声摊薄。

## 8. 明确不需要的

- 涡轮（warrants）全市场扫描——研究对象是**上市正股期权**。
- 2026-08-07 之前的 rank 历史——拉不到真的，别拿静默返回的最新一天冒充。
- 任何美股数据——那是 custody 主线，不走这里。
- 任何交易接口调用。
