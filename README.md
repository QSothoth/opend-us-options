# opend-us-options

**末日期权（0DTE）单笔择时。**上游选好标的、方向和当天到期的合约后，本项目只决定这一张期权当天何时买、何时卖：
每张合约当天争取完成一笔、最多一笔；无信号可以不交易，完成分占综合分的 20%（同一标的可以同时有做多和做空的合约），信号只看当天正股 1 分钟 K 线，盈亏按期权成交价计算，目标是放大盈亏比。

项目规范（Release、训练数据、策略与评测的约定）见 **[AGENTS.md](AGENTS.md)**（`CLAUDE.md` 引用此文件）。

| 文档 | 内容 |
|---|---|
| [docs/DATA.md](docs/DATA.md) | 可用数据集、每日冻结、发布 Release |
| [docs/STANDARD.md](docs/STANDARD.md) | 评测标准：成交模型、场景标签、方向对称加权、门槛与结论 |
| [docs/STRATEGY.md](docs/STRATEGY.md) | 当前策略、研究记录、如何写新策略 |
| [docs/RUNTIME.md](docs/RUNTIME.md) | 状态机、dryrun、下单（run）、status / stop |
| [docs/OPEND_SETUP.md](docs/OPEND_SETUP.md) | 本地 OpenD |

## 快速开始

```bash
# 测试（仅标准库，不连 OpenD）
python3 -m unittest discover -s custody/tests

# 查看注册状态；数据下载与哈希校验见 docs/DATA.md
python3 -m custody strategies

# 下载后离线评测，显式指定策略，避免默认值与报告目录错配
python3 -m custody evaluate --strategy zero_dte_timing_v6.5 \
  --dataset data/custody-0dte-v6.1 --out reports/zero_dte_timing_v6.5/custody-0dte-v6.1
```

数据获取、每日冻结及发布只按 [数据流程](docs/DATA.md) 操作；盘中观察见 [dryrun](docs/RUNTIME.md#4-dryrun)，模拟账户与实盘下单见 [run](docs/RUNTIME.md#5-run)。所有命令：`python3 -m custody`。
只有单边的数据 `custody check` 会以非零退出码拒绝发布；离线评测允许它做诊断。

## 当前状态

- 默认策略和生命周期以 [注册表](custody/strategies/index.json) 为准；默认策略为 v6.5（2026-09-18 起，用户破例设为 `accepted`），其余版本为 candidate。
- 2026-09-20 换用新数据：训练集 `custody-0dte-v6.1`（146 case / 21 个交易日，吸收了原 09-16 验证日），验证集 `custody-eval-2026-09-18-v2`（2026-09-18，28 个 case，含实盘当天的 SNDK），见 [DATA](docs/DATA.md)。五个已注册策略在两个新数据集上各重跑一次，参数和默认版本未改。
- **十份报告没有一份 ACCEPT**，样本外交易日仍是 0 个。训练集上 v6.5 的综合分最高（68.8，对照组 38.2）但 G12 未通过；换数据后它不再像 V5 时期那样全门槛通过。验证集那一天综合分排序是 v6.2 72.4 > v6.3 70.3 > v6.4 67.8 > v6.1 62.7 ≈ v6.5 62.2，默认策略排在最后。完整表格和门槛见 [策略说明](docs/STRATEGY.md#当前结果)。
- **已知问题**：用户 2026-09-18 实盘发现 SNDK 单边上涨（+10.25%）全天没有买入信号。逐分钟回放确认，09:46 时 v6.5 其他条件都满足，只被 v6.4 引入的「突破要来自波动压缩」（`squeeze_lookback`）挡住；之后全天被「不追离开 VWAP 超过 2 ATR」（`max_vwap_atr`）挡住。没有这两条过滤的 v6.1 / v6.2 / v6.3 都在 09:50 买入并拿到 +135.9%。验证集当天对照组涨幅最大的三张（SNDK CALL +184.9%、GOOGL PUT +305.4%、META PUT +98.6%）v6.5 一张都没买。诊断与影响面见 [策略说明](docs/STRATEGY.md#sndk-2026-09-18-没出买入信号的诊断)。
- 比较版本看 [综合分](docs/STANDARD.md) = 收益分 × 80% + 完成分 × 20%；收益分由盈亏比、利润因子、方向对 / 不利时的表现构成，未入场按 0 收益计入，结论仍只看门槛。
- 下一步每天冻结同一行权价的 CALL / PUT，积累样本外交易日；验证集不得用于调参。
- `custody run` 通过 OpenD 下单：`--mode paper` 用模拟账户，`--mode live` 只接受 accepted 策略；dryrun 只读行情、记录模拟成交。

## 安全

- 只有 `custody/broker.py` 使用 OpenD 交易接口；行情、冻结和 dryrun 代码只读（测试强制）。
- 不提交 `.env`、OpenD 登录 / 交易密码、WxPusher SPT、运行数据库。

## License

代码 MIT。Release 中的行情数据是个人研究用小样本，仅用于可复现的离线评测。
