# opend-us-options

**末日期权（0DTE）单笔择时。**上游选好标的、方向和当天到期的合约后，本项目只决定这一张期权当天何时买、何时卖：
每张合约当天争取完成一笔、最多一笔；无信号可以不交易，完成率独立计分（同一标的可以同时有做多和做空的合约），信号只看当天正股 1 分钟 K 线，盈亏按期权成交价计算，目标是放大盈亏比。

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
python3 -m custody evaluate --strategy zero_dte_timing_v4 \
  --dataset data/custody-train-0dte --out reports/zero_dte_timing_v4/custody-train-0dte
```

数据获取、每日冻结及发布只按 [数据流程](docs/DATA.md) 操作；盘中观察见 [dryrun](docs/RUNTIME.md#4-dryrun)，模拟账户与实盘下单见 [run](docs/RUNTIME.md#5-run)。所有命令：`python3 -m custody`。
V4 是单边遗留数据，`custody check` 会以非零退出码拒绝发布；离线评测允许它做诊断。

## 当前状态

- 默认策略和生命周期以 [注册表](custody/strategies/index.json) 为准；当前 v4 为 candidate，尚未达到 ACCEPT。
- v4 取消强制入场，保留确认信号和退出规则；最新 [V4 训练数据报告](reports/zero_dte_timing_v4/custody-train-0dte/REPORT.md) 与 [遗留验证数据报告](reports/zero_dte_timing_v4/custody-eval-2026-09-16/REPORT.md) 同时展示完成分和收益。研究记录见 [策略说明](docs/STRATEGY.md)。
- 下一步每天冻结同一行权价的 CALL / PUT，积累样本外交易日；验证集不得用于调参。
- `custody run` 通过 OpenD 下单：`--mode paper` 用模拟账户，`--mode live` 只接受 accepted 策略；dryrun 只读行情、记录模拟成交。

## 安全

- 只有 `custody/broker.py` 使用 OpenD 交易接口；行情、冻结和 dryrun 代码只读（测试强制）。
- 不提交 `.env`、OpenD 登录 / 交易密码、WxPusher SPT、运行数据库。

## License

代码 MIT。Release 中的行情数据是个人研究用小样本，仅用于可复现的离线评测。
