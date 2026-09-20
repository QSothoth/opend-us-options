# 数据规范（DATA）

实现：`custody/dataset.py`（读取与校验）、`custody/freeze.py`（每日冻结）。总则见 [数据约束](../AGENTS.md#数据约束)。

## 1. Release 是什么

- 数据只通过 GitHub 仓库 `QSothoth/opend-us-options` 的 **Release** 发布：一个 git tag + 一个 zip 附件（附 SHA256）。
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
| `custody-0dte-v5`（V5） | `train/custody` | 当前唯一训练数据 |
| `custody-eval-2026-09-16-v2` | `validation/custody` | 留出验证（2026-09-16），只评测，绝不调参 |
| `custody-eval-2026-09-18` | `validation/custody` | 留出验证（2026-09-18），只评测，绝不调参 |
| `custody-stress-v1` | `diagnostic/stress` | 压力诊断包；**禁止**用于调参或当作正式验证门槛 |

只使用此处登记的数据集。新数据按下文流程冻结并发布，训练集 tag 从 `custody-0dte-v6` 起递增。训练集相对每一份验证集都必须隔离：`custody check --dataset data/custody-0dte-v5 --validation data/custody-eval-2026-09-16-v2` 与 `--validation data/custody-eval-2026-09-18` 均通过（无重叠交易日或会话）。`custody-stress-v1` 不是验证集，不参与隔离门槛。

### V5 详情

- zip SHA256 `5676241cb2498f711977e7c207572ca66f316a0b76bac68b416b2a2f439ad73c`
- `CHECKSUMS.sha256` 的 SHA256 `35875e44318c78ab5b09cd53cac3b22ce26a8a12cb468aeed5efd72e43e97c69`
- 20 个交易日（2026-08-18 → 2026-09-15），126 个 case，63 组 CALL / PUT 两边齐全：SPY 40、QQQ 14、IWM 10、AAPL 10、META 10、MSFT 10、AMD 6、NVDA 6、TSLA 6、AMZN 4、AVGO 4、MU 4、GOOGL 2
- 合约：开盘第一根 K 线价格最近的挂牌行权价（`both_sides_atm_at_open`）；每个 case 都带 `prev_close`、`session_close`；`custody check` 通过
- 按评测标准的场景分布：顺势单边 43、逆势单边 43、震荡 20、先逆后顺 10、先顺后逆 10
- 行情来源：OpenD 期权历史缓存 82 个 case，另 44 个 case（2026-09-10 / 09-11 / 09-14 / 09-15）因期权历史配额用尽改用只读订阅 K_1M 流（已与同合约历史逐 bar 对比一致）
- 跳过：2026-08-17（OpenD 历史 `NN_ProtoRet_SvrFailed`）、2026-09-16（留出验证）；另有 26 个个股会话因当日无对应到期合约或过期链不可查而跳过（见 `manifest.skipped`）
- 来源：2026-09-17 上传到旧 tag `custody-train-0dte` 的附件（zip `e043bf00…`），只把 `manifest.json` 的数据集名改为 `custody-0dte-v5` 并重算其哈希行，行情文件逐字节不变

### 验证集 custody-eval-2026-09-16-v2

- zip SHA256 `5758f49dd9c16fcfea85c9cc41160f01e71f7a341dd6a44214c738a41654dfd7`；文件哈希在 `manifest.json` 的 `series` 里
- 2026-09-16 一个交易日，10 个个股各一组 CALL / PUT（共 20 个 case）：INTC、AMD、TSLA、NVDA、MU、AVGO、AMZN、GOOGL、META、MSFT；`custody check` 通过，与 V5 没有重叠交易日或会话
- 场景分布：顺势单边 8、逆势单边 8、先逆后顺 2、先顺后逆 2、震荡 0，场景覆盖不足
- 当前策略开发截止为 2026-09-16，这份数据不属于它的样本外
- 来源：2026-09-17 上传到旧 tag `custody-eval-2026-09-16` 的附件（zip `ac041ab6…`），只改了 `manifest.json` 的数据集名

### 验证集 custody-eval-2026-09-18

- zip SHA256 `7b0abb604607d720ed016469d43afcfb13cc28e652c50aece8104ea5a49be13b`
- `CHECKSUMS.sha256` 的 SHA256 `589f1055c6e32539645b2f65bfa43d4bc528f4f65354b75d74bb9f65e0c17d65`
- 2026-09-18 一个交易日，14 个标的各一组 CALL / PUT（共 28 个 case）：SPY、QQQ、IWM、AAPL、MSFT、NVDA、TSLA、META、AMZN、GOOGL、AMD、MU、INTC、SNDK；AVGO 因 OpenD 期权历史配额用尽未纳入；`custody check` 通过，与 V5 没有重叠交易日或会话
- 合约：开盘第一根 K 线价格最近的挂牌行权价（`both_sides_atm_at_open`）
- 只评测，绝不调参；与 `custody-eval-2026-09-16-v2` 同属留出验证，可分开或一起做样本外检查
- 发布：https://github.com/QSothoth/opend-us-options/releases/tag/custody-eval-2026-09-18

### 诊断包 custody-stress-v1

- 角色 `diagnostic/stress`：**不是**正式验证集，禁止用于调参，也不当作 ACCEPT / REJECT 门槛
- zip SHA256 `1b59427186381b380fa1f690babaa306a879da77a956c3d264aa13e07616a525`
- `CHECKSUMS.sha256` 的 SHA256 `ca3dcf912b1ab46b56c1ecfbe958b035835260caf1a1c5cb5f817b4f18945667`
- 2026-09-18，4 个 case / 2 组双边：SNDK 开盘 ATM（大涨日）与 SKHY 180 行权价（实盘未入场日）；`custody check` 两边齐全通过
- 用途：诊断漏交易、过滤过严等失败模式；SNDK 同时出现在 `custody-eval-2026-09-18` 中属故意重叠，stress 包仍不得用于拟合
- 发布：https://github.com/QSothoth/opend-us-options/releases/tag/custody-stress-v1

### 已停用的数据集

旧 tag `custody-train-0dte`（V4，单边 36 个 case）和 `custody-eval-2026-09-16`（单边 10 个 CALL）的附件在 2026-09-17 被原地替换，与原登记的哈希（`65398673…`、`58f0af99…`）不再一致，已停用；原内容与结论查 Git 历史。今后不得原地替换附件，数据变化一律发新 tag，也不以本地目录代替 Release。

### 下载并校验

以下 Bash 步骤遇错即停止；使用尚未下载、解压的目标目录，已有 Release 不覆盖。

训练集：

```bash
set -euo pipefail
test ! -e data/custody-0dte-v5
mkdir -p data
gh release download custody-0dte-v5 --repo QSothoth/opend-us-options --pattern custody-0dte-v5.zip --dir data
echo "5676241cb2498f711977e7c207572ca66f316a0b76bac68b416b2a2f439ad73c  data/custody-0dte-v5.zip" | sha256sum -c
python3 -m zipfile -e data/custody-0dte-v5.zip data      # -> data/custody-0dte-v5/
```

验证集（2026-09-16）：

```bash
set -euo pipefail
test ! -e data/custody-eval-2026-09-16-v2
mkdir -p data
gh release download custody-eval-2026-09-16-v2 --repo QSothoth/opend-us-options --pattern custody-eval-2026-09-16-v2.zip --dir data
echo "5758f49dd9c16fcfea85c9cc41160f01e71f7a341dd6a44214c738a41654dfd7  data/custody-eval-2026-09-16-v2.zip" | sha256sum -c
python3 -m zipfile -e data/custody-eval-2026-09-16-v2.zip data
```

验证集（2026-09-18）：

```bash
set -euo pipefail
test ! -e data/custody-eval-2026-09-18
mkdir -p data
gh release download custody-eval-2026-09-18 --repo QSothoth/opend-us-options --pattern custody-eval-2026-09-18.zip --dir data
echo "7b0abb604607d720ed016469d43afcfb13cc28e652c50aece8104ea5a49be13b  data/custody-eval-2026-09-18.zip" | sha256sum -c
python3 -m zipfile -e data/custody-eval-2026-09-18.zip data
```

诊断包（禁止调参）：

```bash
set -euo pipefail
test ! -e data/custody-stress-v1
mkdir -p data
gh release download custody-stress-v1 --repo QSothoth/opend-us-options --pattern custody-stress-v1.zip --dir data
echo "1b59427186381b380fa1f690babaa306a879da77a956c3d264aa13e07616a525  data/custody-stress-v1.zip" | sha256sum -c
python3 -m zipfile -e data/custody-stress-v1.zip data
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
  --repo QSothoth/opend-us-options --title "Custody 0DTE dataset V6 (both sides)" --notes "<窗口、标的、case 数、两个 SHA256>"
```

发布后只在本文件登记 tag、角色、窗口和两个 SHA256（zip 与 `CHECKSUMS.sha256`）。若正式替换当前训练集，再更新 `AGENTS.md` 的可用数据约束；发布验证集不会自动使它成为训练集。
