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
python3 -m custody evaluate --strategy zero_dte_timing_v6.1 \
  --dataset data/custody-0dte-v5 --out reports/zero_dte_timing_v6.1/custody-0dte-v5
```

数据获取、每日冻结及发布只按 [数据流程](docs/DATA.md) 操作；盘中观察见 [dryrun](docs/RUNTIME.md#4-dryrun)，模拟账户与实盘下单见 [run](docs/RUNTIME.md#5-run)。所有命令：`python3 -m custody`。
只有单边的数据 `custody check` 会以非零退出码拒绝发布；离线评测允许它做诊断。

## 当前状态

- 默认策略和生命周期以 [注册表](custody/strategies/index.json) 为准；默认策略为 v6.5（2026-09-18 起，用户破例设为 `accepted`，评测结论仍是 PROVISIONAL，见下）；其余版本为 candidate。
- v6.1 用较短的确认入场和 1–2 ATR 紧止损；最新 [V5 训练数据报告](reports/zero_dte_timing_v6.1/custody-0dte-v5/REPORT.md) 与 [验证数据报告](reports/zero_dte_timing_v6.1/custody-eval-2026-09-16-v2/REPORT.md) 同时展示完成分和收益。研究记录见 [策略说明](docs/STRATEGY.md)。
- 并行候选 v6.2（非默认）在 v6.1 上加两条入场过滤：不追离开 VWAP 超过 2 ATR 的行情、估算权利金至少 5 个 1m ATR。[训练集报告](reports/zero_dte_timing_v6.2/custody-0dte-v5/REPORT.md) 方向对称平均 −1.8%、盈亏比 3.47，仍因整体亏钱（G11）和同一标的 CALL / PUT 合起来赚钱的配对不多于对照组（G12）REJECT；使用时需显式传 `--strategy zero_dte_timing_v6.2`。
- 比较版本看 [综合分](docs/STANDARD.md) = 收益分 × 80% + 完成分 × 20%；收益分由盈亏比、利润因子、方向对 / 不利时的表现构成，未入场按 0 收益计入，结论仍只看门槛。训练集 v6.2 59.6、v6.1 49.7、对照组 42.5（上游方向对 60% 时 63.3 / 52.6 / 47.1）。完成分分别为 45.2 / 84.9 / 98.4，另列方向正确时参与率用于诊断。
- 新研究候选 v6.3（非默认）在 v6.2 上增加突破量能确认和 10 分钟无进展退出。[训练报告](reports/zero_dte_timing_v6.3/custody-0dte-v5/REPORT.md) 综合分 63.0、完成分 35.7、盈亏比 4.43、平均收益 −0.33%，但利润因子 0.96，仍为 REJECT；方向正确时只参与 22/53（41.5%）。[R11 选择交叉验证](reports/zero_dte_timing_v6.3/custody-0dte-v5/selection.json) 保留当时未纳入完成分的旧口径，未证明稳定优于 v6.2；本次评分调整不重新选择候选。仅供显式指定 `--strategy zero_dte_timing_v6.3` 研究使用。
- 研究候选 v6.4（非默认）在 v6.2 上只接受波动压缩后的突破，加 10 分钟无进展离场和估算浮盈 +50% 后锁住一半。[训练报告](reports/zero_dte_timing_v6.4/custody-0dte-v5/REPORT.md) 是第一个 G3–G12 全部通过的已注册版本（PROVISIONAL，没有样本外交易日）：综合分 66.6、盈亏比 4.80、平均收益 +2.34%、利润因子 1.53，但只做 29/126 笔；[验证集](reports/zero_dte_timing_v6.4/custody-eval-2026-09-16-v2/REPORT.md) 那一天仍亏 6.4%。R15 [选择审计](reports/zero_dte_timing_v6.4/custody-0dte-v5/selection.json) 显示 20 折中 19 折选中它，但相对 v6.3 的配对区间跨零。使用时显式指定 `--strategy zero_dte_timing_v6.4`。
- 默认策略与研究基线 v6.5 = v6.4 去掉「VWAP 有利侧站稳 5 根」，交易从 29 笔增加到 68 笔。[训练报告](reports/zero_dte_timing_v6.5/custody-0dte-v5/REPORT.md) 门槛全部通过（PROVISIONAL）：综合分 71.9、盈亏比 4.68、平均收益 +3.24%、利润因子 1.32；[验证集](reports/zero_dte_timing_v6.5/custody-eval-2026-09-16-v2/REPORT.md) 那一天亏 15.2%，差于 v6.4。它未满足 R16 预登记的选择规则，由用户决定登记（见 [审计](reports/zero_dte_timing_v6.5/custody-0dte-v5/selection.json)）。2026-09-18 用户决定未经 ACCEPT 把它改为 `accepted`，只用于每单 1 张的实盘链路试运行（见 [策略说明](docs/STRATEGY.md)）。
- 下一步每天冻结同一行权价的 CALL / PUT，积累样本外交易日；验证集不得用于调参。
- `custody run` 通过 OpenD 下单：`--mode paper` 用模拟账户，`--mode live` 只接受 accepted 策略；dryrun 只读行情、记录模拟成交。

## 安全

- 只有 `custody/broker.py` 使用 OpenD 交易接口；行情、冻结和 dryrun 代码只读（测试强制）。
- 不提交 `.env`、OpenD 登录 / 交易密码、WxPusher SPT、运行数据库。

## License

代码 MIT。Release 中的行情数据是个人研究用小样本，仅用于可复现的离线评测。
