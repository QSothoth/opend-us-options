# 项目规范（AGENTS.md）

> 本文件与 `CLAUDE.md` 内容必须完全一致（`custody/tests/test_docs.py` 强制）。修改时两处同时改。
> 任何人或 AI 助手在本仓库工作前先读完本文件；与本文件冲突的旧文档、旧代码一律以本文件为准。

## 1. 这个项目做什么

**上游**（另一个环节）已经选好：标的、方向、当天到期的具体期权合约。
**本项目**只负责这一张末日期权（0DTE）当天的**择时**：

1. 输入是一张合约。**每张合约当天必做且只做一笔**：买入一次、卖出一次，收盘前平掉。同一个标的可以同时有多张合约（比如一张 CALL 做多、一张 PUT 做空），各自一笔、互不影响；
2. 信号**只看当天正股 1 分钟 K 线**；
3. 盈亏**只按这张期权的成交价**衡量；
4. 目标是**放大盈亏比**：方向对了尽量拿住涨幅，方向错了尽早止损；入场时点要适配顺势、低开高走 / 高开低走（先逆后顺 / 先顺后逆）、震荡等常见日内形态。

本项目**不**选标的、**不**选方向、**不**选合约、**不**卖出开仓（永远是买方：LONG = 买 CALL，SHORT = 买 PUT）。

## 2. 术语

| 术语 | 含义 |
|---|---|
| 0DTE / 末日期权 | 到期日 == 交易日的期权合约 |
| case | 一张合约在一个交易日上的一次择时任务（symbol + contract + trade_date），方向由合约的 CALL/PUT 决定 |
| 一笔 / round trip | 一次买入 + 一次卖出；部分成交也仍算这一笔 |
| 策略 strategy | 引擎代码 `custody/engines/<engine>.py` + 不可变参数文件 `custody/strategies/<strategy_id>.json` |
| 评测标准 standard | `docs/STANDARD.md` + `custody/evaluate.py`，所有策略用同一把尺子 |
| 对照组 | 「不择时」的笨办法：09:35 直接买入，拿到 15:45 卖出。用同样的数据和成交规则算出来，策略必须比它好才算择时有用 |
| **Release** | GitHub 仓库 `QSothoth/opend-us-options` 上的 Release：一个 git tag + zip 附件（+ sha256）。**Release 是数据的唯一发布形式**，代码不走 Release。Release 发布后不可修改，新数据 = 新 tag |
| 数据集 dataset | 解压后的 Release 目录，布局见 `docs/DATA.md` |
| 代 generation（V1…V4…） | 训练数据的版本序号，见第 3 节 |
| 样本外 OOS | 交易日晚于策略 `developed_on.sessions_through` 的 case |

## 3. 数据规范

### 3.1 训练数据：只有 V4 可用

| 代 | 名称 | 位置 | 内容 | 状态 |
|---|---|---|---|---|
| V1 | `custody-train-2026-09-08_11` | 本地，未发布 | 4 天 × SPY/QQQ/AAPL，12 个 case | **废弃**：只是管道测试规模 |
| V2 | `custody-train-v2window-paired` | 本地缓存，未发布 | 830 个 case，DTE 0–99 | **废弃**：长 DTE 是数据可得性造成的，不是末日期权 |
| V3 | `custody-train-dte4` | Release `custody-train-dte4` | 124 个 case，DTE 0–4 | **废弃**：混入非 0DTE；方向来自用了当日收盘的场景标签（未来函数） |
| **V4** | `custody-train-0dte` | Release `custody-train-0dte` | 36 个 case / 14 个交易日 / 10 个标的，全部真 0DTE | **当前唯一训练数据** |

V4 固定值：zip SHA256 `65398673c6617ef0a013c1795936016404babe4a1d4fc1777e16502c2621c7d3`；
解压后 `CHECKSUMS.sha256` 的 SHA256 `deb7432fb8417fa05c88535248a3af816cd1227f1ef374c4f430ffb363e926db`（评测报告里记录的就是这个）。

**V4 的已知缺陷（必须记住）**：方向是按「全天成交量更大的一侧」选的，这是收盘后才知道的信息。
实测 36 个 case 里只有 6 个方向与当天开盘→15:45 的走势一致。因此：

- 不允许直接看 V4 的原始平均收益来比较策略，必须用评测标准里的**方向对称加权**；
- V4 的「顺势单边」只有 2 个、「先顺后逆」只有 1 个，场景覆盖不足，任何策略在 V4 上最多只能得到 PROVISIONAL。

**其他 Release 都不是训练数据，也不是评测数据，不要使用**：`eval-data-v1`、`eval-data-v2`（只有正股 K 线，旧的 underlying-proxy 研究遗留）；`custody-eval-2026-09-14`（3 个 case，其中 2 个不是 0DTE，已退役）。

### 3.2 数据铁律

1. **只用真实 OpenD 数据。**合成数据只允许出现在单元测试里（`custody/tests/helpers.py`），永远不能用于评测或写进报告。
2. **只收真 0DTE**：expiry == trade_date。
3. **配对**：每个 case 必须有同日完整的正股 1m（常规交易时段每分钟一根）+ 该合约的 1m。
4. **方向不得由当天结果决定**（全天收益、全天成交量、事后场景标签都不行）。V5 起每个 (标的, 交易日) 同时冻结平值 CALL 和 PUT 两边（`selection = both_sides_atm_at_open`）。
5. **每个数据文件由 `CHECKSUMS.sha256` 固定**，评测时自动校验，不一致直接报错。
6. **Release 不可变**：不要在解压出来的 Release 目录里改任何文件。新数据在工作目录里用 `custody freeze` 累积，攒够后发布新 Release，tag 命名 `custody-0dte-v5`、`custody-0dte-v6`……（流程见 `docs/DATA.md`）。
7. **数据文件不进 git**：放在 `data/`（已 gitignore）。
8. **OpenD 配额**：只读；历史 K 线限速 50 次/30 秒；期权历史按 (标的, 到期日) 占配额，过期周权很快会被 OpenD 丢弃——**每个交易日收盘后当天就要 freeze**，错过就补不回来。

## 4. 策略规范

1. **输入只有**：当天已完成的正股 1m K 线、方向、所交易合约的行权价、交易日历（开收盘时间）。
   **禁止**：期权价格、前一交易日及更早的数据、日线、场景标签、未完成或未来的 bar、随机数、跨实例的全局状态。
2. **必做一笔**：每个参数文件都必须有 `must_enter_before_close_minutes`（在这之前必须 ENTER）和 `flatten_before_close_minutes`（在这之前必须 EXIT，平台强制至少 15 分钟）。平台会独立执行这两个截止时间。
3. **一天一笔**：止损出场后当天不再入场。
4. **不以固定时间点或固定价格作为入场逻辑。**截止时刻的强制入场只是「必做一笔」的兜底，不是策略本体。
5. **注册文件不可变**：`custody/strategies/index.json` 固定每个文件的 SHA256。改任何参数 = 新的 `strategy_id`（例如 `zero_dte_timing_v3`）+ 新文件，旧文件不动。
6. **状态写在 `index.json`**（参数文件里不能有状态）：`candidate`（可 dryrun / paper）→ `accepted`（只有评测结论为 ACCEPT 才能改成这个；live 只接受 accepted）→ `retired`（保留做审计，不能再建任务）。
7. **当前唯一可用策略**：`zero_dte_timing_v3`（`candidate`，默认）；`zero_dte_timing_v1`、`zero_dte_timing_v2` 已 `retired`。说明见 `docs/STRATEGY.md`。
8. **防过拟合的研究流程（必须遵守）**：
   - 先写清假设和理由，再**事先写下**要试的少量候选（每轮不超过 5 个）和选择规则，然后才跑评测；跑完不得改规则；
   - 只做有结构性理由的改动，参数取整数或常见值，不做网格搜参；
   - 用完整评测标准（含 G8–G10 防过拟合门槛）比较候选；
   - 对「从这些候选里挑一个」的过程做留一天交叉验证，报告样本外估计；
   - 每一轮的全部候选、结果（包括失败的）都记进 `docs/STRATEGY.md`。

已删除、禁止复活的旧策略：`orb_rvol_rsi_1m_v1`、`retest_rvol_adx_5m_v1`、`custody_trend_1m_v1`、`custody_payoff_1m_v2`、`custody_payoff_aggressive_1m_v2`（固定时间入场 / 可以整天不交易 / 在 V3 上用 3 万组参数搜出来 / 用正股收益代替期权收益）。

## 5. 评测规范（摘要，完整定义见 `docs/STANDARD.md`）

- **唯一口径**：`python3 -m custody evaluate --dataset <数据集目录> --out reports/<strategy_id>/<dataset>/`
- **成交**：决策后至少 1 分钟、第一根有成交量的期权 1m K 线，按收盘价向不利方向让出该 K 线振幅的 25%，单边手续费 $0.65/张。
- **主口径**：方向对称加权（消除数据集里方向对错比例的影响）；**主指标**：盈亏比。
- **门槛**：G1 完成率 100%；G2 没有偷看未来数据；G3 平均每笔收益好于对照组；G4 盈亏比 ≥ 2.0；G5 逆势单边时亏得比对照组少；G6 方向正确时平均赚钱；G7 盈亏比高于对照组；G8 前后两半交易日都赢对照组；G9 去掉任意一天仍赢对照组；G10 参数上下浮动 25% 仍赢对照组；G11 整体赚钱（利润因子 > 1）。G3、G7 按收益率和按美元都要成立。
- **结论等级**：INVALID（G1/G2 不过）/ REJECT（G3–G11 有不过）/ PROVISIONAL（门槛全过但场景覆盖不足或样本外 < 20 个交易日）/ ACCEPT。
- **卖不出去的仓位**按收盘内在价值结算（`settled_at_expiry`），策略和对照组同一规则。
- **禁止**：用正股收益 × 乘数代替期权收益；用合成数据评测；删掉失败的 case 再算；看完评测结果再改标准让策略通过。
- 报告放在 `reports/<strategy_id>/<dataset>/`（`report.json` + `REPORT.md`），和策略版本一起提交。

## 6. 运行时规范

- 本仓库**没有、也不接入**真实券商下单。任何代码都不得引用 `OpenSecTradeContext`、`place_order`、`unlock_trade`、`modify_order`（测试静态扫描强制）。
- `dryrun` 只读 OpenD 行情，把下单意图记在本地 SQLite，默认按实时买卖价标记为**模拟成交**，便于观察完整一天；`--intent-only` 只记意图。
- 服务端约束：同一账户 + 同一合约 + 同一交易日只能有一个任务（同一标的的不同合约，包括一多一空，可以同时进行）；只接受 0DTE 合约；live 只接受 `accepted` 策略。
- 密钥（OpenD 密码、WxPusher SPT、HTTP token）只放环境变量或未跟踪的本地文件，**绝不提交**。

## 7. 仓库结构

```text
AGENTS.md / CLAUDE.md     项目规范（本文件，两份一致）
README.md                 概览与快速开始
docs/DATA.md              数据集布局、代际、freeze 与发布流程
docs/STANDARD.md          评测标准（完整定义）
docs/STRATEGY.md          当前策略说明、研究记录、如何写新策略
docs/RUNTIME.md           状态机、API、dryrun
docs/OPEND_SETUP.md       OpenD 本地环境
custody/
  strategy.py             策略接口（Decision / 截止时间 / build_strategy）
  indicators.py           当天 1m 因果指标（VWAP / EMA / ATR / 开盘区间）
  engines/                策略引擎代码（zero_dte_timing.py）
  strategies/             注册的策略参数文件（不可变）+ index.json（哈希与状态）
  dataset.py              数据集布局、校验、读取
  evaluate.py             评测标准实现
  freeze.py               每日只读 OpenD 冻结（两边）
  service.py controller.py http.py models.py ports.py   运行时状态机与接口
  job_request.schema.json  建任务请求的 JSON Schema
  dryrun.py opend.py marketdata.py                      只读行情与 dryrun
  tests/                  单元测试（仅标准库）
reports/                  各策略版本在各数据集上的评测报告
data/                     本地数据（gitignore）
```

## 8. 常用命令

```bash
# 测试（仅标准库，不连 OpenD）
python3 -m unittest discover -s custody/tests

# 取 V4
mkdir -p data && gh release download custody-train-0dte --repo QSothoth/opend-us-options --dir data
echo "65398673c6617ef0a013c1795936016404babe4a1d4fc1777e16502c2621c7d3  data/custody-train-0dte.zip" | sha256sum -c
unzip -q data/custody-train-0dte.zip -d data

# 评测（离线）
python3 -m custody strategies
python3 -m custody evaluate --dataset data/custody-train-0dte --out reports/zero_dte_timing_v3/custody-train-0dte

# 每个交易日收盘 20 分钟后冻结当天数据（只读 OpenD，需要 futu-api）
python3 -m custody freeze --dataset data/custody-0dte-work

# 盘中观察一个任务（只读 OpenD，绝不下单）
python3 -m custody dryrun --symbol US.QQQ --direction LONG --contract US.QQQ260916C705000
```

## 9. 协作约定

- 文档和说明用中文；代码、标识符、提交信息用英文。
- 运行时和评测只用 Python 标准库；`futu-api` 只有 `freeze` / `dryrun` 需要。
- 任何改动后跑完整测试；改了策略或标准，要重新生成 `reports/` 下受影响的报告并一起提交。
- 修改评测标准（`custody/evaluate.py` 顶部常量、成交模型、门槛）必须在同一次改动里更新 `docs/STANDARD.md`，并重跑所有已注册策略的报告。
- 不复活第 3.1、4 节列出的废弃数据和策略。
