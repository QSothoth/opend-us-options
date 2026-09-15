# 数据隔离与实盘接入依赖

本次只加强数据边界与行情恢复，不根据验证结果调整参数。两个策略配置的字节哈希保持不变。训练和调参仍使用 `custody-train-dte4` 的全部 124 个 case；`custody-eval-2026-09-14` 的 3 个 case 继续作为固定验证集。

## 训练、缓存、验证的边界

- `DATA_PINS.json` 固定两个 Release 的 manifest、cases、训练校验清单的 SHA256，以及角色、日期、case 数与合约/日期成员摘要。目录改名、伪造 role 或混用旧正股数据都不能通过。先验证元数据身份，再校验其声明的市场数据字节。
- 全部训练入口共用 `data_boundary.py`：search、prepare、refine、global_pressure、confirm、diagnostics、replay。后两阶段不再直接信任裸 cache。
- `CACHE_BINDING.json` 绑定完整缓存字节、case 元数据、数据 pins 和特征计算源码哈希。加载时解析的就是刚校验过的内存字节；缓存损坏、元数据改变、代码改变或验证角色混入会拒绝，必须从训练 Release 重建。
- 冻结工具先要求两个策略均有完整 124-case 训练回放，再保存代码、策略及训练报告哈希。验证入口先核对冻结记录和全部策略哈希，再打开指定验证 Release。报告只能写入 `validation_only` 目录，拒绝覆盖已有报告；搜索入口不读取该目录。
- 这是应用层内容与角色隔离，不是对拥有仓库写权限者的安全沙箱。历史全量搜索记录不因此变成未见样本。三个验证 case 已被反复报告，按用户要求继续使用，但不据此排名或调参。

## 运行时确实需要什么

| 输入 | 用途 | 获取路径 | 不可用时 |
|---|---|---|---|
| 当天正股 RTH 已完成 1m OHLCV，从 09:31 到当前完整分钟 | 所有择时指标 | OpenD 实时 K 线；不足/报错时请求同一天历史 1m，支持已有分页适配器 | 报告缺失分钟和首个缺口，重试；不使用昨天的数据补齐 |
| 指定期权当前 bid/ask、报价时间 | 下单价格与执行检查 | 已有 quote/snapshot 适配器 | 无有效报价就不能伪造成交；保持既有错误/重试流程 |
| 指定合约到期日、类型、乘数和可交易状态 | 合约校验、DTE 时钟 | 已有合约 snapshot/代码解析 | 元数据不完整或不可交易时拒绝任务 |
| 当前交易日开闭市时间 | 时间边界、日终退出 | 已有交易日历适配器 | 无法确定交易时段时显式报错 |

**运行时不需要**训练/验证 ZIP、研究缓存、历史期权 K 线、历史日 K、昨天的正股 K 线、跨日 ATR/RVOL、IV/Greeks、研究用情景标签、NumPy、Numba 或研究模块。这里仅指两个 `intraday_v2` 策略；仓库保留的旧研究策略仍有自己的跨日依赖，不能拿它们的启动命令替代 v2。

v2 的 ATR14、RSI、EMA、VWAP 和量能均从当天数据计算。协议字段 `Frame.daily_atr` 为兼容旧接口保留名字，但 v2 写入的是当天 1m ATR，同时在诊断中明确名为 `atr_1m`。

实时源现在缓存当天完整前缀，只补缺失分钟。盘中首次启动或进程重启会通过当前 K 线窗口/当天历史补齐前缀；跨日自动丢弃旧缓存。实时接口失败也会尝试当天历史，不再阻止独立回退。SDK 返回的昨天和未完成 K 线在数据源边界外被过滤；策略入口直接收到跨日、未来、日 K、错误标的或错误周期数据则拒绝。

部分美股期权有 16:00 后的同日期权 K 线。离线数据入口允许这些同日记录存在，但保持原先收盘前成交回放边界；它们不进入正股择时，也不用于补一个不存在的收盘前成交。

## 接入验证范围

82 项测试通过，新增覆盖：伪造训练角色、缓存与源码污染、日 K/昨天/未来/错误标的/错误周期拒绝、运行时禁止研究依赖导入、实时接口失败、盘中启动与重启一致、缺分钟拒绝、跨日缓存与状态隔离。

还实际把指定的 3-case Release 传给七个训练入口，均被拒绝。重跑两个策略全部 124 个训练 case，核对与已发布结果一致，然后冻结，再在原 3 个验证 case 上复测。

这证明代码依赖和模拟恢复路径可用，**不证明尚未连接的实盘账户已经具备行情权限、历史配额或服务可用性**。正式接入时必须在实际 OpenD 账户检查这些能力；任何平台停机或缺行情都不可能由策略代码保证“必成交”。保持 dryrun，本次没有连接 OpenD 或券商下单。

## 当前命令

```bash
python3 research/custody_v2/prepare.py --train /path/to/custody-train-dte4
python3 research/custody_v2/replay.py --train /path/to/custody-train-dte4 --strategy custody_payoff_1m_v2 --out /tmp/robust-train.json
python3 research/custody_v2/replay.py --train /path/to/custody-train-dte4 --strategy custody_payoff_aggressive_1m_v2 --out /tmp/aggressive-train.json
python3 research/custody_v2/freeze_for_validation.py --train /path/to/custody-train-dte4 --training-reports /tmp/robust-train.json /tmp/aggressive-train.json --out /tmp/FROZEN.json
python3 research/custody_v2/validate_frozen.py --slice /path/to/custody-eval-2026-09-14 --freeze /tmp/FROZEN.json --out /tmp/validation_only/report.json
python3 -m unittest custody.tests.test_data_purity custody.tests.test_adaptive custody.tests.test_baseline custody.tests.test_runtime custody.tests.test_dryrun -q
```

继续训练开发时，`refine.py` 和 `global_pressure.py` 也必须显式传入 `--train /path/to/custody-train-dte4`；不存在跳过角色或缓存哈希的 CLI 开关。若变更特征源码，先重建缓存，再重新完成训练回放和冻结。
