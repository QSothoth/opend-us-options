# 数据规范（DATA）

实现：`custody/dataset.py`（读取与校验）、`custody/freeze.py`（每日冻结）。总则见 [数据约束](../AGENTS.md#数据约束)。

## 1. Release 是什么

- 数据只通过 GitHub 仓库 `QSothoth/s-alpha` 的 **Release** 发布：一个 git tag + 一个 zip 附件（附 SHA256）。
- Release **不可变**：发布后不改文件、不换附件。数据有任何变化都发新 tag。
- 代码不走 Release；代码只在 git 分支里。
- 解压后的 Release 目录就是一个**数据集**，评测直接读取它。

## 2. 数据集布局

```text
<dataset>/
  manifest.json          数据集名、角色、窗口、跳过记录等元数据
  cases.json             {"cases": [ 每个期权合约一条 ]}
  CHECKSUMS.sha256       每个数据文件一行 "<sha256>  <相对路径>"
  underlying/<SYMBOL>.csv   正股 1m K 线（可包含多个交易日）
  option/<CONTRACT>.csv     期权 1m K 线
```

CSV 列：`code,close_time,interval,open,high,low,close,volume`。`close_time` 是带美东时区偏移的 ISO-8601 K 线**收盘**时间（第一根常规时段 K 线是 `09:31:00-04:00`）。
Release 里可能同时有 parquet 副本；本项目只读 CSV。

`cases.json` 每条 case 的字段：

| 字段 | 必需 | 说明 |
|---|---|---|
| `symbol` | 是 | 例如 `US.SPY` |
| `contract` | 是 | Futu 期权代码，例如 `US.SPY260914C600000` |
| `trade_date` | 是 | 交易日，必须等于合约到期日 |
| `right` / `direction` | 否 | 如有，必须与合约代码一致（CALL = LONG，PUT = SHORT） |
| `prev_close` | V5 起必需 | 正股上一交易日收盘价，用于评测形态标签，禁止传给策略；freeze 未取到时写 null |
| `session_close` | V5 起必需 | 当日收盘时间（提前收盘日为 13:00） |
| `selection` | V5 起必需 | 合约选择方式；V5 起必须是 `both_sides_atm_at_open` |

### CALL 和 PUT 必须一起验证（统一要求）

训练集和验证集都适用：

- 每个 (标的, 交易日) 同时包含**同一行权价**的 CALL 和 PUT 两张 0DTE 合约（行权价取开盘第一根 1m 开盘价最近的挂牌行权价），各算一个 case；
- 方向不得由当天结果挑选。两边都评测，当天无论涨跌，方向对和方向错的合约各占一半，结果与上游选方向的能力无关；这时「方向对错各半」的加权与简单平均完全一致；
- 两边不全的数据集只能做诊断，评测报告会标出「两边不全」，结论最高 PROVISIONAL；
- 发布前必须通过 `python3 -m custody check --dataset <目录>`：哈希全部一致、每个 case 行情完整、两边齐全，否则退出码非 0。

### 训练集必须排除验证集（硬隔离）

验证集是留出集，绝不参与拟合；训练集绝不能包含任何验证交易日或验证的 `(标的, 交易日)` 会话：

- 训练集 manifest 角色为 `train/custody`，验证集为 `validation/custody`，两者不可相同；
- `set(训练 trade_date) ∩ set(验证 trade_date) == ∅`；
- `set(训练 (symbol, trade_date)) ∩ set(验证 (symbol, trade_date)) == ∅`。

发布训练集前，除单数据集检查外还必须运行隔离检查（任一角色不对、或任何日期/会话重叠都会使退出码非 0，从而阻止发布）：

```bash
python3 -m custody check --dataset <训练目录> --validation <验证目录>
```

单侧 `custody check` 只保证一个目录自身合法；`--validation` 才会把「训练不得混入留出验证日」变成发布前的自动门槛。脚本可用 `custody.dataset.isolation_report` 复用同一判定。

读取时的强制检查（元数据和哈希在打开目录时检查，行情在加载 case 时检查；失败直接报错）：

1. 每个数据文件都有 SHA256：`CHECKSUMS.sha256`，或（验证集 Release 的布局）`manifest.json` 里 `series` 条目逐文件的 `sha256`；全部一致，且案例读取的文件必须在其中；
2. 每个 case 都是真 0DTE，合约属于该标的，存储的方向与合约一致；
3. 没有重复的 (合约, 交易日)；
4. 正股 K 线覆盖开盘到收盘的每一分钟；
5. 合约在交易时段内至少有一根成交量和收盘价均大于 0 的 K 线。

`check` 校验上述项目及同一行权价的两边是否存在；它不能仅凭数据文件证明行情来源真实或行权价确为挂牌最近值。合约选择必须使用 freeze 流程，不能把检查通过当成对任意外部数据的背书。

## 3. 可用数据集

| 名称 | 角色 | 用途 |
|---|---|---|
| `custody-0dte-v6.1`（V6.1） | `train/custody` | 当前唯一训练数据 |
| `custody-eval-2026-09-18-v2` | `validation/custody` | 当前唯一留出验证，只评测，绝不调参 |

只使用此处登记的数据集。新数据按下文流程冻结并发布，训练集 tag 从 `custody-0dte-v6` 起递增。`custody check --dataset data/custody-0dte-v6.1 --validation data/custody-eval-2026-09-18-v2` 通过（无重叠交易日或会话）。

### V6.1 详情

- zip SHA256 `49032a1b9f8cc5fef3c9a7d7fee9fef99e220e06e12c7f89d849ce70fddc04c1`
- `CHECKSUMS.sha256` 的 SHA256 `38b2d5d8b852deae9bc1aa599b41fb57410274c283834ea86367177960656206`
- 21 个交易日（2026-08-18 → 2026-09-16），146 个 case，73 组 CALL / PUT 两边齐全：SPY 40、QQQ 14、META 12、MSFT 12、AAPL 10、IWM 10、AMD 8、NVDA 8、TSLA 8、MU 6、AMZN 6、AVGO 6、GOOGL 4、INTC 2
- 与 V5 的区别：吸收了原验证集 `custody-eval-2026-09-16-v2` 的 2026-09-16（20 个 case），并给每个 case 加了事后标签
- 事后标签（`cases.json` 的 `labels` 与 `case_labels.json`，`ex_post: true`）：`path_scenario`、`market_shape`/`gap`、`range_bucket`、`orb15`、日内分段、成交量占比。**只用于统计和报告，禁止传给策略**
- 场景分布：顺势单边 51、逆势单边 51、震荡 20、先逆后顺 12、先顺后逆 12
- `custody check` 通过；`manifest.json` 的 `window.end`（2026-09-15）和 `underlyings`（缺 INTC）没有随吸收的 09-16 更新，以 `sessions` 和 `cases.json` 为准
- 取代 `custody-0dte-v6`（同样 146 个 case，无标签）

### 验证集 custody-eval-2026-09-18-v2

- zip SHA256 `8bf9eb37d7372dce30c4ca37188e11c45a5fddb744ef0c5de120a61f3d139253`
- `CHECKSUMS.sha256` 的 SHA256 `fba36ab960189e6fe80660629455774627915d1b8982361a324ec203892fc2c0`
- 2026-09-18 一个交易日，14 个标的各一组 CALL / PUT（共 28 个 case）：AAPL、AMD、AMZN、GOOGL、INTC、IWM、META、MSFT、MU、NVDA、QQQ、SNDK、SPY、TSLA；`custody check` 通过，与 V6.1 没有重叠交易日或会话
- 场景分布：顺势单边 8、逆势单边 8、震荡 4、先逆后顺 4、先顺后逆 4；一个交易日无法判定 G8 / G9，场景样本也不足，结论最高 PROVISIONAL
- 带与 V6.1 同样的事后标签（`ex_post`，不得进入策略）
- 取代 `custody-eval-2026-09-18`（同样 28 个 case，无标签）

### 已被取代的数据集

- `custody-0dte-v5`（train，126 case / 20 日）和 `custody-eval-2026-09-16-v2`（validation，20 case）：2026-09-20 起分别由 V6.1 和 `custody-eval-2026-09-18-v2` 取代。09-16 这一天已并入训练集，不再是留出验证；两个 Release 本身仍然有效，只是不再是登记的训练 / 验证数据。
- `custody-0dte-v6` 和 `custody-eval-2026-09-18`：内容与 v6.1 / -v2 的行情相同，只差事后标签，不再单独登记。

### 已停用的数据集

旧 tag `custody-train-0dte`（V4，单边 36 个 case）和 `custody-eval-2026-09-16`（单边 10 个 CALL）的附件在 2026-09-17 被原地替换，与原登记的哈希（`65398673…`、`58f0af99…`）不再一致，已停用；原内容与结论查 Git 历史。今后不得原地替换附件，数据变化一律发新 tag，也不以本地目录代替 Release。

### 下载并校验

以下 Bash 步骤遇错即停止；使用尚未下载、解压的目标目录，已有 Release 不覆盖。

训练集：

```bash
set -euo pipefail
test ! -e data/custody-0dte-v6.1
mkdir -p data
gh release download custody-0dte-v6.1 --repo QSothoth/s-alpha --pattern 'custody-0dte-v6.1.zip' --dir data
echo "49032a1b9f8cc5fef3c9a7d7fee9fef99e220e06e12c7f89d849ce70fddc04c1  data/custody-0dte-v6.1.zip" | sha256sum -c
python3 -m zipfile -e data/custody-0dte-v6.1.zip data      # -> data/custody-0dte-v6.1/
```

验证集：

```bash
set -euo pipefail
test ! -e data/custody-eval-2026-09-18-v2
mkdir -p data
gh release download custody-eval-2026-09-18-v2 --repo QSothoth/s-alpha --pattern 'custody-eval-2026-09-18-v2.zip' --dir data
echo "8bf9eb37d7372dce30c4ca37188e11c45a5fddb744ef0c5de120a61f3d139253  data/custody-eval-2026-09-18-v2.zip" | sha256sum -c
python3 -m zipfile -e data/custody-eval-2026-09-18-v2.zip data
```


## 4. 每日冻结（V5 起的数据来源）

先配置 [OpenD 环境](OPEND_SETUP.md)。OpenD 很快就会丢弃过期周权的历史，期权历史配额也有限，所以**每个交易日收盘 20 分钟后当天就要冻结**：

```bash
python3 -m custody freeze --dataset data/custody-0dte-work
python3 -m custody check --dataset data/custody-0dte-work
```

每个 (标的, 交易日) 的处理：

1. 读取完整的常规时段正股 1m（不完整则跳过并记录原因）；
2. 读取日线得到昨收；
3. 查当日到期的期权链，没有当日到期则跳过并记录；
4. 取离第一根 K 线开盘价最近的行权价，**CALL 和 PUT 两边**都拉 1m，各写一个 case；
5. 追加写入 CSV（按时间去重）、更新 `cases.json` / `manifest.json`、重算 `CHECKSUMS.sha256`，最后重新校验哈希与 case 元数据；完整行情和两边要求仍须运行 `custody check`。

重复运行同一天不会重复添加已有 case，但会重写行情、更新时间并追加跳过记录，不保证字节不变。默认标的见 `custody/freeze.py` 的 `DEFAULT_SYMBOLS`，可用 `--symbols` 覆盖。

工作目录 `data/custody-0dte-work` **不是** Release，可以一直追加；不要指向任何已解压的 Release；命令无法自动识别所有已发布目录。训练和验证使用不同工作目录，角色不能混用。

### 生成验证集

验证集和训练集用同一个命令、同样两边一起冻结，只是角色不同、交易日要晚于策略的开发截止日，并且绝不用来调参：

```bash
set -euo pipefail
python3 -m custody freeze --dataset data/custody-eval-2026-09-17 --date 2026-09-17 --role validation/custody \
  --symbols US.INTC,US.AMD,US.TSLA,US.NVDA,US.MU,US.AVGO,US.AMZN,US.GOOGL,US.META,US.MSFT
python3 -m custody check --dataset data/custody-eval-2026-09-17
```

OpenD 保留过期周权的时间很短，最好当天收盘 20 分钟后就冻结；多天的验证集就对同一个目录每天追加一次。

## 5. 发布新 Release

攒够一批交易日（建议至少 20 个新交易日，才够做样本外判定），并且 `custody check` 通过后：

先停止向工作目录追加数据，选择未使用的新 tag。以下以 V6 为例，目录或附件已存在就停止，避免复制嵌套或更新旧 zip：

```bash
set -euo pipefail
test ! -e data/custody-0dte-v6
test ! -e data/custody-0dte-v6.zip
test ! -e data/custody-0dte-v6.zip.sha256
cp -r data/custody-0dte-work data/custody-0dte-v6
python3 -m custody check --dataset data/custody-0dte-v6      # 必须 "ok": true（含 CALL/PUT 两边齐全）
sha256sum data/custody-0dte-v6/CHECKSUMS.sha256
(cd data && python3 -m zipfile -c custody-0dte-v6.zip custody-0dte-v6 && sha256sum custody-0dte-v6.zip > custody-0dte-v6.zip.sha256)
cat data/custody-0dte-v6.zip.sha256
```

核对检查结果、窗口、标的、case 数和上面两个 SHA256，将它们填写到发布说明后再执行发布：

```bash
gh release create custody-0dte-v6 data/custody-0dte-v6.zip data/custody-0dte-v6.zip.sha256 \
  --repo QSothoth/s-alpha --title "Custody 0DTE dataset V6 (both sides)" --notes "<窗口、标的、case 数、两个 SHA256>"
```

发布后只在本文件登记 tag、角色、窗口和两个 SHA256（zip 与 `CHECKSUMS.sha256`）。若正式替换当前训练集，再更新 `AGENTS.md` 的可用数据约束；发布验证集不会自动使它成为训练集。

## 附录：港股近月流量研究数据（非 custody Release）

与上文 0DTE custody 冻结/Release 流程无关。港股正股期权排行与异动研究切片放在仓库内：

[`studies/hk_near_expiry_flow/dataset/`](../studies/hk_near_expiry_flow/dataset/)（`manifest.json` + CSV）。不走 `data/` Release 规则；仅供后续自行筛选，不接入 `custody check`。


## 附录：盯盘信号研究数据（非 custody Release）

`studies/watch_signal/`（盯盘 `watch/` 的信号研究）的数据，按用途分成 5 个 Release。只供该研究使用，**不接入 `custody check` / `evaluate`**，
也不受上文 0DTE 冻结流程约束；但同样不可变，变化发新 tag。每个 zip 内有 `manifest.json`（来源、窗口、行数、字段说明）和 `CHECKSUMS.sha256`。

| tag | 角色 | 内容 | zip SHA256 | CHECKSUMS.sha256 的 SHA256 |
|---|---|---|---|---|
| `watch-hk1m-train-v1` | 训练 / 选择 | 140 只港股通 1m（QFQ），2025-12-01 → 2026-08-14，172 天，774.9 万行 | `cd1af1ab66a6591f33b3b230e047a94c6a5fc010fd0ef8122e6b443618ab49ce` | `93b010478ea49b474de4b5352ba8e3884967808db455ce32748b217aeb4aab26` |
| `watch-hk1m-valid-v1` | 验证（只评测） | 同 140 只，2026-08-17 → 09-23，28 天，129.8 万行 | `ffc9cc3f0be2197cfd67d681aef22d3e9f5a4e94dbccf9d530cb4576d7045ad8` | `0283e569bb34d9b0dcfda12b7e80e0b9c2d84c92a54ff2c2b29a1afcdd5d3c6a` |
| `watch-hk-index1m-v1` | 大盘参照 | 恒指 2025-12-01 → 2026-09-23；恒生科技、盈富 2026-06-01 → 09-23 | `f140204d499ac8ee389cd8ffdbd43f97aab3a659fb9fd7b597059db911f5500d` | `30a6c836373c2564f94c5d5f349d5b408b35336bcbfe549187a74eef44d1183d` |
| `watch-hk-daily-v1` | 日线背景（W6） | 140 只 + 恒指日 K（QFQ），2024-06-03 → 2026-09-23，569 天，7.7 万行 | `e40e5e6c417059da7e2f35f3fe595afedb57a54a7666faa51c1a6355f7aa8e8f` | `d12bb68a266fa1a7e9b783848813fe09e2b36f3a42f7eec84a16952c1ce9d4f2` |
| `watch-hk-flow-2026-09-24-partial` | 资金流单日测试 | 140 只分钟资金流 + 同日 1m，**只到 13:53 HKT（未收盘）** | `0ed5c3c13801954e0b07ae3b7f1d5178891b7a77dd3fe12c8462d310bcd0be50` | `b2c7d6c5f11fd01bf7d023e5e139d0979785e8002cd969e265e777ba5584b387` |

```bash
gh release download watch-hk1m-train-v1 --repo QSothoth/s-alpha --dir data
cd data && sha256sum -c watch-hk1m-train-v1.zip.sha256 && unzip watch-hk1m-train-v1.zip
```

全部为只读行情；1m K 线只拉了 30 天内已扣费的标的，没有消耗新的历史 K 线额度。
