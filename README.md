# opend-us-options

**末日期权（0DTE）单笔择时。**上游选好标的、方向和当天到期的合约后，本项目只决定这一张期权当天何时买、何时卖：
每张合约当天必做且只做一笔（同一标的可以同时有做多和做空的合约），信号只看当天正股 1 分钟 K 线，盈亏按期权成交价计算，目标是放大盈亏比。

项目规范（Release、训练数据、策略与评测的约定）见 **[AGENTS.md](AGENTS.md)**（与 `CLAUDE.md` 一致）。

| 文档 | 内容 |
|---|---|
| [docs/DATA.md](docs/DATA.md) | 数据集布局、V1–V4 代际、每日冻结、发布 Release |
| [docs/STANDARD.md](docs/STANDARD.md) | 评测标准：成交模型、场景标签、方向对称加权、门槛与结论 |
| [docs/STRATEGY.md](docs/STRATEGY.md) | 当前唯一策略 `zero_dte_timing_v1`、研究记录、如何写新策略 |
| [docs/RUNTIME.md](docs/RUNTIME.md) | 状态机、控制 API、dryrun |
| [docs/OPEND_SETUP.md](docs/OPEND_SETUP.md) | 本地 OpenD |

## 快速开始

```bash
# 测试（仅标准库，不连 OpenD）
python3 -m unittest discover -s custody/tests

# 取训练数据 V4 并评测当前策略
mkdir -p data && gh release download custody-train-0dte --repo QSothoth/opend-us-options --dir data
echo "65398673c6617ef0a013c1795936016404babe4a1d4fc1777e16502c2621c7d3  data/custody-train-0dte.zip" | sha256sum -c
unzip -q data/custody-train-0dte.zip -d data
python3 -m custody evaluate --dataset data/custody-train-0dte --out reports/zero_dte_timing_v1/custody-train-0dte

# 需要 OpenD + futu-api：每个交易日收盘后冻结当天数据；盘中只读观察一个任务
python3 -m custody freeze --dataset data/custody-0dte-work
python3 -m custody dryrun --symbol US.QQQ --direction LONG --contract US.QQQ260916C705000
```

## 当前状态

- 策略 `zero_dte_timing_v1`：状态 `candidate`，V4 评测结论 **REJECT**（盈亏比 1.40 未达到 2；平均每笔 −22.3%，好于「09:35 买入拿到 15:45」对照组的 −37.0%；方向错的日子从 −98.5% 降到 −49.2%）。详见 [报告](reports/zero_dte_timing_v1/custody-train-0dte/REPORT.md)。
- V4 方向选择有偏、场景覆盖不足、没有样本外数据；下一步是每天 freeze 两边数据，攒够后发布 V5。
- 没有真实券商下单连接；`dryrun` 绝不下单。

## 安全

- 只读 OpenD；本仓库任何代码不得引用下单 / 解锁交易接口（测试强制）。
- 不提交 `.env`、token、OpenD 密码、WxPusher SPT。

## License

代码 MIT。Release 中的行情数据是个人研究用小样本，仅用于可复现的离线评测。
