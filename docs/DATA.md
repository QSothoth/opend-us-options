# 数据规范（DATA）

实现：`custody/dataset.py`（读取与校验）、`custody/freeze.py`（每日冻结）。总则见 `AGENTS.md` 第 3 节。

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
| `prev_close` | V5 起必需 | 正股上一交易日收盘价，用于「低开高走」等形态标签 |
| `session_close` | V5 起必需 | 当日收盘时间（提前收盘日为 13:00） |
| `selection` | V5 起必需 | 合约选择方式；V5 起必须是 `both_sides_atm_at_open` |

### CALL 和 PUT 必须一起验证（统一要求）

训练集和验证集都适用：

- 每个 (标的, 交易日) 同时包含**同一行权价**的 CALL 和 PUT 两张 0DTE 合约（行权价取开盘第一根 1m 开盘价最近的挂牌行权价），各算一个 case；
- 方向不得由当天结果挑选。两边都评测，当天无论涨跌，方向对和方向错的合约各占一半，结果与上游选方向的能力无关；这时「方向对错各半」的加权与简单平均完全一致；
- 两边不全的数据集只能做诊断，评测报告会标出「两边不全」，结论最高 PROVISIONAL；
- 发布前必须通过 `python3 -m custody check --dataset <目录>`：哈希全部一致、每个 case 行情完整、两边齐全，否则退出码非 0。

读取时的强制检查（任何一项不满足直接报错）：

1. 每个数据文件都有 SHA256：`CHECKSUMS.sha256`，或（验证集 Release 的布局）`manifest.json` 里 `series` 条目逐文件的 `sha256`；全部一致，且案例读取的文件必须在其中；
2. 每个 case 都是真 0DTE，合约属于该标的，存储的方向与合约一致；
3. 没有重复的 (合约, 交易日)；
4. 正股 K 线覆盖开盘到收盘的每一分钟；
5. 合约在交易时段内至少有一根有成交量的 K 线。

## 3. 代际

| 代 | tag / 名称 | 用途 |
|---|---|---|
| V1 | `custody-train-2026-09-08_11`（未发布） | 废弃 |
| V2 | `custody-train-v2window-paired`（未发布） | 废弃 |
| V3 | `custody-train-dte4` | 废弃（混 DTE；方向来自用了当日收盘的场景标签） |
| **V4** | `custody-train-0dte` | **当前唯一训练数据** |
| V5+ | `custody-0dte-v5`、`custody-0dte-v6` …… | 由每日 freeze 累积后发布；两边都冻结 |
| — | `eval-data-v1`、`eval-data-v2` | 只有正股 K 线的旧研究数据，不使用 |
| 验证 | `custody-eval-2026-09-16` | **当前验证集**：只评测，绝不调参；单边遗留，不满足两边要求（见下） |
| — | `custody-eval-2026-09-14` | 3 个 case、2 个不是 0DTE，已退役 |

### V4 详情

- zip SHA256 `65398673c6617ef0a013c1795936016404babe4a1d4fc1777e16502c2621c7d3`
- `CHECKSUMS.sha256` 的 SHA256 `deb7432fb8417fa05c88535248a3af816cd1227f1ef374c4f430ffb363e926db`，共 94 个文件
- 14 个交易日（2026-08-17 → 2026-09-14），36 个 case：SPY 12、QQQ 5、IWM 2、AAPL 5、MSFT 4、META 4、NVDA 1、TSLA 1、AMD 1、MU 1
- 合约：开盘第一根 K 线价格最近的挂牌行权价；每个 (标的, 交易日) 只保留了一边
- **已知偏差**：保留哪一边是按「全天期权成交量更大」决定的（收盘后才知道）。下跌日开盘平值 CALL 会变成便宜的虚值、成交张数暴增，所以量大的一侧多数是亏钱的一侧：36 个里只有 6 个方向与当天开盘→15:45 走势一致，开盘买入持有到 15:45 的平均收益 −78%、中位 −97%
- 按评测标准的场景分布：逆势单边 19、先逆后顺 8、震荡 6、顺势单边 2、先顺后逆 1；没有昨收
- 结论：V4 只能配合方向对称加权使用，且场景覆盖不足，任何策略在 V4 上最多 PROVISIONAL

### 验证集 custody-eval-2026-09-16

- zip SHA256 `58f0af996a33f042fd81751f3b68408266bd224be2530df10f44230e67165a31`；文件哈希在 `manifest.json` 的 `series` 里（共 40 个 csv/parquet）
- 2026-09-16 一个交易日，10 张单一个股真 0DTE：INTC、AMD、TSLA、NVDA、MU、AVGO、AMZN、GOOGL、META、MSFT，全部是 CALL
- 晚于所有策略的开发截止日（2026-09-14），对所有已注册策略都是样本外
- **不满足「CALL 和 PUT 一起验证」**（`custody check` 失败：10 组里 0 组两边齐全）
- **已知偏差**：方向仍按「全天期权成交量更大的一侧」选（10 张都选了 CALL），而当天这 10 张全部是「方向错」（逆势单边 8、先顺后逆 2），对照组平均 −99.3%。因此「方向对错各半」的指标、盈亏比、整体赚钱等门槛都无法判断，只能验证「方向错时亏得少不少」
- 获取：`gh release download custody-eval-2026-09-16 --repo QSothoth/opend-us-options --dir data && unzip -q data/custody-eval-2026-09-16.zip -d data`

获取：

```bash
mkdir -p data && gh release download custody-train-0dte --repo QSothoth/opend-us-options --dir data
echo "65398673c6617ef0a013c1795936016404babe4a1d4fc1777e16502c2621c7d3  data/custody-train-0dte.zip" | sha256sum -c
unzip -q data/custody-train-0dte.zip -d data      # -> data/custody-train-0dte/
```

## 4. 每日冻结（V5 起的数据来源）

OpenD 很快就会丢弃过期周权的历史，期权历史配额也有限，所以**每个交易日收盘 20 分钟后当天就要冻结**：

```bash
pip install futu-api                       # 只有 freeze / dryrun 需要
python3 -m custody freeze --dataset data/custody-0dte-work
python3 -m custody freeze --dataset data/custody-0dte-work --date 2026-09-16 --symbols US.SPY,US.QQQ,US.AAPL
```

每个 (标的, 交易日) 的处理：

1. 读取完整的常规时段正股 1m（不完整则跳过并记录原因）；
2. 读取日线得到昨收；
3. 查当日到期的期权链，没有当日到期则跳过并记录；
4. 取离第一根 K 线开盘价最近的行权价，**CALL 和 PUT 两边**都拉 1m，各写一个 case；
5. 追加写入 CSV（按时间去重）、更新 `cases.json` / `manifest.json`、重算 `CHECKSUMS.sha256`，最后整体重新校验。

重复运行同一天是幂等的。默认标的：SPY、QQQ、IWM、AAPL、MSFT、NVDA、TSLA、META、AMZN、GOOGL、AMD、MU。

工作目录 `data/custody-0dte-work` **不是** Release，可以一直追加；它不能是解压出来的 V4 目录。

### 生成验证集

验证集和训练集用同一个命令、同样两边一起冻结，只是角色不同、交易日要晚于策略的开发截止日，并且绝不用来调参：

```bash
python3 -m custody freeze --dataset data/custody-eval-2026-09-17 --date 2026-09-17 --role validation/custody \
  --symbols US.INTC,US.AMD,US.TSLA,US.NVDA,US.MU,US.AVGO,US.AMZN,US.GOOGL,US.META,US.MSFT
python3 -m custody check --dataset data/custody-eval-2026-09-17
```

OpenD 保留过期周权的时间很短，最好当天收盘 20 分钟后就冻结；多天的验证集就对同一个目录每天追加一次。

## 5. 发布新 Release

攒够一批交易日（建议至少 20 个新交易日，才够做样本外判定），并且 `custody check` 通过后：

```bash
cp -r data/custody-0dte-work data/custody-0dte-v5
python3 -m custody check --dataset data/custody-0dte-v5      # 必须 "ok": true（含 CALL/PUT 两边齐全）
(cd data && zip -qr custody-0dte-v5.zip custody-0dte-v5 && sha256sum custody-0dte-v5.zip > custody-0dte-v5.zip.sha256)
gh release create custody-0dte-v5 data/custody-0dte-v5.zip data/custody-0dte-v5.zip.sha256 \
  --repo QSothoth/opend-us-options --title "Custody 0DTE dataset V5 (both sides)" --notes "<窗口、标的、case 数、两个 SHA256>"
```

发布后在 `AGENTS.md`、`CLAUDE.md` 和本文件的代际表里登记 tag 和两个 SHA256（zip 与 `CHECKSUMS.sha256`）。
