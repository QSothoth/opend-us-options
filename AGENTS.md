# 项目规范

> 开工前读完本文件。规范只维护在 `AGENTS.md`；`CLAUDE.md` 仅保留一行 `@AGENTS.md` 引用（测试强制）。
> 本文件规定项目边界与协作要求；冲突的旧文档、旧代码以本文件为准。

## 项目边界

上游已选好标的、方向和具体合约。本项目只做一张当天到期的期权（0DTE）的日内择时，目标是放大盈亏比：方向对时拿住涨幅，方向错时尽早止损，并适配顺势、反转和震荡。

- 一个 case = `symbol + contract + trade_date`。每张合约当天争取完成一笔、最多一笔：有信号才买入，买入后收盘前退出；无信号可以不交易，禁止为完成率兜底买入。部分成交仍算这一笔，止损后不再入场。
- 永远是期权买方：LONG = 买 CALL，SHORT = 买 PUT，不选标的、方向、合约，不卖出开仓。同一标的的不同合约各自独立。
- 策略输入只有当天已完成的正股 1m K 线、方向、行权价、交易日历（开收盘时间）。禁止期权价格、前日及更早数据、日线、场景标签、未完成或未来 bar、随机数、跨实例全局状态。
- 不以固定时刻或固定价格作为入场逻辑。当前策略只要求 `flatten_before_close_minutes`，平台至少提前 15 分钟请求退出；到点禁止新买入。禁止为完成率添加强制入场规则。
- 盈亏只按所交易期权的成交价计算。禁止正股收益乘系数代替期权收益、删除失败 case、看完结果改标准让策略通过。

## 文档分工

细节只在对应文档维护；修改相关功能前读对应文档，不把表格、哈希和操作步骤复制回本文件。

| 入口 | 维护内容 |
|---|---|
| [README.md](README.md) | 项目概览、离线快速开始 |
| [docs/DATA.md](docs/DATA.md) | 可用数据集、固定哈希、冻结与发布流程 |
| [docs/STANDARD.md](docs/STANDARD.md) | 成交模型、对照组、指标、G1–G11 与结论定义 |
| [docs/STRATEGY.md](docs/STRATEGY.md) | 当前策略行为、研究记录与修改步骤 |
| [docs/RUNTIME.md](docs/RUNTIME.md) | 状态机、API、只读 dryrun |
| [docs/OPEND_SETUP.md](docs/OPEND_SETUP.md) | OpenD 环境与行情连接 |
| [custody/strategies/index.json](custody/strategies/index.json) | 策略默认值、状态与参数文件 SHA256；查询用 `python3 -m custody strategies` |
| `reports/<strategy_id>/<dataset>/` | 评测产物 `report.json` + `REPORT.md` |

## 数据约束

- 只用真实 OpenD 数据，expiry 必须等于 trade_date。每个 case 必须有同日完整常规时段正股 1m 和该合约的期权 1m。合成数据只允许用于单元测试（`custody/tests/helpers.py`），不得用于评测或报告。
- 新训练集和验证集都必须包含每个 (标的, 交易日) 的 **CALL 和 PUT**：同一行权价，取开盘第一根 1m 开盘价最近的挂牌行权价，各算一个 case。不得按全天收益、成交量或事后标签挑方向。
- 每个数据文件必须由 `CHECKSUMS.sha256` 或 `manifest.json` 的 `series` 中逐文件 SHA256 固定；不一致或读取未固定的文件直接报错。
- 当前唯一训练数据是 V4 `custody-train-0dte`；验证集 `custody-eval-2026-09-16` 只评测、绝不调参。两者都是按全天成交量选方向的单边遗留数据，最多 PROVISIONAL。V4 必须用方向对称加权，不能用原始平均收益比较策略；验证集全部方向错，只能检验同批对比与亏损控制。只使用 [DATA](docs/DATA.md) 登记的可用数据集。
- 新数据用 `python3 -m custody freeze` 在 `data/` 工作目录累积；验证集加 `--role validation/custody`。每个交易日收盘 20 分钟后当天冻结，历史请求按 50 次 / 30 秒限速；过期周权可能无法补取。
- 数据唯一发布形式是 `QSothoth/opend-us-options` 的 GitHub Release（tag + zip + SHA256）。发布前必须通过 `python3 -m custody check --dataset <目录>`。Release 及其解压目录不可修改，新数据发新 tag；代码不走 Release，数据不进 git。

## 策略与评测

- 策略 = `custody/engines/<engine>.py` + 不可变参数文件。改参数必须用新 `strategy_id`、新文件，在 `index.json` 登记 SHA256；在用文件不可原地改写。
- 状态只写在 `index.json`：`candidate` 可 dryrun / paper；只有评测 ACCEPT 才能改为 `accepted`；`retired` 禁止新建任务。live 只接受 accepted，但本仓库没有真实下单连接。
- 不保留废弃策略、旧报告或兼容分支；删除不再使用的代码和注册文件，历史通过 Git 查询，不复活已删除的策略。
- 研究前先写假设、结构性理由、每轮不超过 5 个候选和选择规则，然后才评测；跑完不得改规则。参数用整数或常见值，不做网格搜索。
- 候选必须用完整标准比较（含 G8–G10）；对候选选择过程做留一天交叉验证，报告样本外估计。全部候选、结果和失败都记录在 `docs/STRATEGY.md`。
- 唯一评测入口：`python3 -m custody evaluate --strategy <id> --dataset <目录> --out reports/<id>/<dataset>/`。主口径是方向对称加权，主指标是盈亏比；对照组采用相同数据和成交规则。
- 完成分 = 完成买入并卖出的 case 数 / 全部 case 数 × 100，独立展示，不是硬门槛。无信号、买入未成交、退出未成交分别记录；结算计盈亏但不算完成卖出。已入场表现单列，整体比较保留全部 case（未入场贡献零），不得靠丢弃未交易样本改善指标。
- 结论按 [STANDARD](docs/STANDARD.md) 判定：INVALID / REJECT / PROVISIONAL / ACCEPT。无法计算的门槛记不适用；两边不全、场景不足或样本外不足 20 个交易日不能 ACCEPT。样本外指交易日晚于 `developed_on.sessions_through`。
- 卖不出去的仓位在离线评测中按收盘内在价值结算（`settled_at_expiry`），策略和对照组同规则。

## 运行与安全

- 本仓库没有、也不接入真实券商下单。代码不得引用 `OpenSecTradeContext`、`place_order`、`unlock_trade`、`modify_order`（测试静态扫描）。
- dryrun 只读 OpenD，将意图记入本地 SQLite，默认按实时买卖价标记模拟成交；`--intent-only` 只记意图。
- 同一账户 + 同一合约 + 同一交易日只能有一个任务，只接受 0DTE；不同合约互不影响。
- OpenD 密码、WxPusher SPT、HTTP token 等密钥只放环境变量或未跟踪的本地文件，绝不提交。

## 修改与验证

- 文档和说明用中文；代码、标识符、提交信息用英文。
- 运行时和评测只用 Python 标准库；`futu-api` 只有 freeze / dryrun 需要。
- 任何改动后跑完整测试：`python3 -m unittest discover -s custody/tests`（不连 OpenD）。
- 改策略行为或参数，重新生成受影响的报告并随改动提交。改评测口径（常量、成交模型、标签、权重、门槛），必须同步 `custody/evaluate.py`、`docs/STANDARD.md` 和测试，并重跑所有已注册策略的报告。仅修正文案、不改变口径时不重跑报告。
- 文档更新核对代码、CLI 和已提交报告；不改写历史研究结果，不把操作约定描述成尚未实现的自动保证。
