# 港股通开盘窗口扫描（hk_open_scan）

在**竞价结束前**和**开盘后 15 分钟末**两个时点，对港股通全体正股（662 只）做横截面排序，
找当天量价形态最强、可以追涨的标的。只读 OpenD 行情，不下单，不进 `custody` 策略注册表。

与 `watch/`（固定几个标的的盘中盯盘）和 `studies/auction_strength/`（三只票的竞价脉冲）
的区别只有一个，但是决定性的：**这里做横截面选择**。`watch/README.md` 自己引的
Zarattini/Barbon/Aziz 已经说明了为什么——*"abnormal participation, not the breakout
pattern alone, drove profitability"*，而那句话在盯固定标的时用不上。

## 目录

| 文件 | 内容 |
|---|---|
| [reports/OPEND_CAPABILITIES_CN.md](reports/OPEND_CAPABILITIES_CN.md) | OpenD 港股能力实测：三种额度、全市场接口、竞价历史的有无、tick 成本 |
| [reports/WATCH_REVIEW_CN.md](reports/WATCH_REVIEW_CN.md) | 对 `watch/smc_watch.py` 的复核，每条都有实测数字 |
| [notes/R1_PREREG.md](notes/R1_PREREG.md) / [notes/R2_PREREG.md](notes/R2_PREREG.md) | 两轮研究的运行前登记 |
| [reports/R1_R2_RESULTS_CN.md](reports/R1_R2_RESULTS_CN.md) | 结果与判定 |
| `code/capture.py` | 竞价窗口实时采集（**必须每个交易日跑，错过补不回来**） |
| `code/baseline.py` | 收盘后建滚动基准，零额度 |
| `code/fetch_1m.py` | 拉回测用 1m 历史，默认拒绝消耗新额度 |
| `code/backtest.py` | 离线回测，只用标准库 |
| `code/scan.py` | 两个窗口的实盘排序 |
| `code/review_watch.py` | 复核 `watch/` 的度量脚本 |
| [notes/R3_PREREG.md](notes/R3_PREREG.md) / [notes/R4_PREREG.md](notes/R4_PREREG.md) | ORB 只做多：R3 大盘 140 只（OpenD 1m），R4 中小盘 515 只（Yahoo 5m） |
| [reports/R3_RESULTS_CN.md](reports/R3_RESULTS_CN.md) / [reports/R4_RESULTS_CN.md](reports/R4_RESULTS_CN.md) | 两轮都是六档全部失败 |
| `code/scratch/` | R3/R4 及成交量门槛测试的原始脚本，原样存档；数据路径指向当时会话的临时目录，复跑要先改路径 |

## 一句话结论

**在可测的这批大盘港股通上，追涨是负期望，而且追得越晚越差。** 69 个交易日、8405 个标的-日、
两轮共 10 个候选，按预登记规则**全部失败**；「开盘段异常放量、09:45 追进去」这一支在 500 次
随机置换中一次都没赢过（p = 1.000）。同一个跳空排序在竞价时点还有 +31.9 bp/天，等到 09:45
只剩 +6.9 bp，去掉最好的一天变成 −10.0 bp。

**最大的保留**不是样本长度，而是 universe：这 140 只流通市值中位 827 亿、最小 54 亿，
全是大盘股，而「异常参与度」效应在中小盘上最强。之后 R3 把完整 ORB 结构搬过来只做多，
R4 再换到剩下的中小盘（515 只、Yahoo 5m），**两轮十二个档同样全部失败**，中小盘没有救活追涨。

完整表格、口径检查与局限见 [R1_R2_RESULTS_CN.md](reports/R1_R2_RESULTS_CN.md)。

## 下一步（按性价比排）

1. **每个交易日跑 `capture.py`**。竞价过程没有历史，不跑就永远没有数据。
2. **每天收盘后跑 `baseline.py`**。零额度，约 10 个交易日后全部 662 只都有滚动基准，
   届时中小盘也能进回测，正面回答「效应是不是只在小盘上」。
3. 中小盘已由 R4 用 Yahoo 5m 零额度测过（全部失败），不必再为此消耗 `request_history_kline` 额度
   （**与美股 0DTE 每日 freeze 共用**）。

## 数据边界（先读这个）

港股 1 分钟历史**第一根是 09:30，而那根就是开盘竞价成交本身**（实测 2939/2940 个标的-日是
O=H=L=C 的十字，价格等于日线开盘价）。连续交易从 09:31 开始。

因此两个窗口的可回测性完全不同：

| 窗口 | 决策时刻 | 有历史吗 | 本仓库怎么处理 |
|---|---|---|---|
| 一 | 竞价结束前 ≈ 09:19 | **没有**（09:00–09:22 在 OpenD 里不存在） | 只能回测它的**上界**（R2：假装已知竞价结果）；真数据靠 `capture.py` 逐日积累 |
| 二 | 09:45 | 有 | 直接回测（R1） |

分钟级资金流（`get_capital_flow(INTRADAY)`）同样**只返回最近一个交易日**，传 `start`/`end`
会被忽略，所以它也只能前瞻采集，本轮不作为回测特征。

## 用法

```bash
PY=/opt/futu-opend/venv/bin/python     # 装了 futu-api 的解释器

# 每个交易日 08:50 起（这一步不做，竞价窗口就永远没有数据）
$PY code/capture.py --out captures/ --universe universe.json

# 收盘后累积基准（零额度）。OpenD 的订阅额度不由 unsubscribe 或关闭连接释放，
# 所以每次只取 300 只、按最久未更新轮换，几个交易日覆盖完 662 只。
$PY code/baseline.py --out baseline.json --universe universe.json

# 已经有 1m 历史时直接播种，不必等轮换（不连 OpenD）
python3 code/baseline.py --out baseline.json --from-bars hk1m.csv.gz

# 两个窗口的实盘排序
$PY code/scan.py --window auction --baseline baseline.json --top 20
$PY code/scan.py --window open15  --baseline baseline.json --top 20

# 离线回测（不需要 OpenD）
python3 code/backtest.py --bars hk1m.csv.gz --ticks ticks.csv
```

`scan.py` 只输出**排序**，不输出「买这个」。缺少滚动基准的因子会被标成缺失并把该标的移出主榜，
而不是当成中性值参与打分——`studies/auction_strength/` 的 `data_grade` 压分是同一个用意。

2026-09-22 09:47 的实盘冒烟测试：662 只全市场扫描 1 秒完成，140 只有基准的里 56 只入榜，
榜首腾讯 +5.44%、开盘段成交额 6.5 倍于自身常态；其余 603 只因无基准被如实列为缺因子。
**这只验证管路，不代表这些名字值得买**——回测结论见上。

## 额度纪律

`request_history_kline` 的 300 只 / 30 天额度是**美股 0DTE 主线每天 `freeze` 要用的**，
过期周权补不回来。本目录的回测只用「30 天内已经扣过费」的 140 只港股通标的，
`fetch_1m.py` 默认拒绝为新标的扣费，要扣必须显式 `--allow-new`。
本轮全部工作后额度仍是 188 已用 / 112 剩余，一分未动。
