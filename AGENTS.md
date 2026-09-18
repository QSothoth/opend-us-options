# 项目规范

> 开工前读完。本文件是唯一规范（`CLAUDE.md` 只有一行 `@AGENTS.md`，测试强制），与旧文档、旧代码冲突时以本文件为准；细节只写在下表对应文档里，不复制回来。

## 项目边界

上游已选好标的、方向和合约；本项目只为这一张当天到期的期权（0DTE）做日内择时，目标是放大盈亏比：方向对时拿住涨幅，方向错时尽早止损，适配顺势、反转和震荡。

- 一个 case = `symbol + contract + trade_date`，最多一笔：有信号才买入，收盘前卖出；无信号可以不交易，禁止为完成率兜底或强制入场。部分成交也算这一笔。
- 永远是买方：LONG 买 CALL，SHORT 买 PUT；不选标的、方向、合约，不卖出开仓；同一标的的不同合约互相独立。
- 策略输入只有：当天已完成的正股 1m K 线、方向、行权价、开收盘时间。禁止期权价格、前日及更早数据、日线、场景标签、未完成或未来的 bar、随机数、跨实例状态。
- 入场不依赖固定时刻或固定价格。平台至少在收盘前 15 分钟（`flatten_before_close_minutes`）要求退出，到点禁止买入。
- 盈亏只按期权成交价计算；禁止用正股收益折算、删除失败 case、看完结果改标准。

## 文档分工

| 文档 | 内容 |
|---|---|
| [README.md](README.md) | 概览、离线快速开始 |
| [docs/DATA.md](docs/DATA.md) | 已登记数据集、哈希、冻结与发布 |
| [docs/STANDARD.md](docs/STANDARD.md) | 成交模型、对照组、指标、门槛与结论 |
| [docs/STRATEGY.md](docs/STRATEGY.md) | 当前策略、研究记录、修改步骤 |
| [docs/RUNTIME.md](docs/RUNTIME.md) | 状态机、dryrun、下单（run）、status / stop |
| [docs/OPEND_SETUP.md](docs/OPEND_SETUP.md) | OpenD 环境 |
| [custody/strategies/index.json](custody/strategies/index.json) | 默认策略、状态、参数文件 SHA256 |
| `reports/<strategy_id>/<dataset>/` | 评测报告 |

## 数据约束

- 只用真实 OpenD 数据，且只用 [DATA](docs/DATA.md) 登记的数据集；合成数据只用于单元测试。
- 每个 case 是真 0DTE（expiry = trade_date），有同日完整常规时段的正股 1m 和该合约期权 1m；每个数据文件都由 SHA256 固定，不一致或未固定直接报错。
- 新数据集每个 (标的, 交易日) 必须同时有 **CALL 和 PUT**（同一行权价：开盘第一根 1m 开盘价最近的挂牌行权价），不得按收益、成交量或事后标签挑方向。
- 验证集只评测，绝不调参。只有单边的数据集只能做诊断，结论最高 PROVISIONAL。
- 每个交易日收盘后当天用 `custody freeze` 冻结（过期周权补不回来）。数据只以 GitHub Release（tag + zip + SHA256）发布，发布前必须通过 `python3 -m custody check`；Release 及其解压目录不可修改，新数据发新 tag，数据不进 git。

## 策略与评测

- 策略 = `custody/engines/<engine>.py` + 不可变参数文件。改参数用新 `strategy_id` 和新文件，并在 `index.json` 登记 SHA256；同一引擎上的小改动用小版本号（如 v6 → v6.1），大改动才升大版本。
- 状态只写在 `index.json`，只有评测 ACCEPT 才能改为 `accepted`。不保留废弃策略、旧报告或兼容分支；历史查 Git，不复活已删除的策略。
- 研究前先写假设、结构性理由、每轮 ≤ 5 个候选和选择规则，跑完不改规则；参数用整数或常见值，不做网格搜索。候选用完整标准（含 G8–G10）评测，先比未通过门槛数、再比综合分（判为优于基线还要求上游命中率 60% 时综合分也更高），选择过程做留一天交叉验证并报告样本外估计；全部候选、结果和失败记入 `docs/STRATEGY.md`。
- 唯一评测入口：`python3 -m custody evaluate --strategy <id> --dataset <目录> --out reports/<id>/<dataset>/`。主口径方向对称加权，主指标盈亏比；结论只看门槛，完成分不是门槛；比较版本用综合分（收益分占 80%、完成分占 20%；收益分由盈亏比、利润因子、方向对 / 不利时的表现加权，不买入按 0 收益计入，见 [STANDARD](docs/STANDARD.md) 第 10 节）；整体比较保留全部 case（未入场贡献零）。
- 结论按 [STANDARD](docs/STANDARD.md) 判定；两边不全、场景不足或样本外交易日不足 20 个都不能 ACCEPT。

## 运行与安全

- `custody dryrun` 只读 OpenD，只记意图和模拟成交；`custody run` 通过 OpenD 下单（`paper` = 模拟账户，`live` = 真实资金，只接受 `accepted` 策略）。只有 `custody/broker.py` 可以使用交易接口，其他模块不得引用 `OpenSecTradeContext`、`place_order`、`unlock_trade`、`modify_order`（测试扫描）。
- OpenD 登录密码、交易密码（`FUTU_TRADE_PASSWORD` / `FUTU_TRADE_PASSWORD_MD5`）、WxPusher SPT 等密钥只放环境变量或未跟踪的本地文件，绝不提交，也不作为命令行参数。

## 修改与验证

- 文档用中文；代码、标识符、提交信息用英文。运行时和评测只用标准库，`futu-api` 仅 freeze / dryrun / run 需要。
- 任何改动后跑 `python3 -m unittest discover -s custody/tests`。
- 改策略行为或参数：重新生成受影响的报告并一起提交。改评测口径（常量、成交模型、标签、权重、门槛）：同步 `custody/evaluate.py`、`docs/STANDARD.md` 和测试，并重跑所有已注册策略的报告。只改文案不重跑。
- 更新文档前核对代码、CLI 和已提交报告；不改写历史研究结果，不把约定写成尚未实现的自动保证。
