# 项目规范

> 开工前读完本文件，再读你要改的那个目录的 `AGENTS.md`。规范分三层：本文件（定位、能力地图、全仓库规则）→ 各能力目录的 `AGENTS.md`（该能力的边界与规则）→ `docs/` 与各目录 README（细节）。与旧文档、旧代码冲突时以规范为准；细节只写在对应文档里，不复制回规范。每个目录的 `CLAUDE.md` 只有一行 `@AGENTS.md`（测试强制）。

## 仓库定位与能力地图

只读 OpenD 行情为主的个人交易工具仓库。**核心能力两项**，其余是未达生产的研究切片。

| 层级 | 能力 | 目录 | 规范 | 状态 |
|---|---|---|---|---|
| 核心 | **美股期权日内单笔交易**（主要是末日期权 0DTE）：上游给定标的、方向、合约，本仓库只做入场 / 离场时机；数据冻结 → 策略评测 → dryrun / 下单 | `custody/` | [custody/AGENTS.md](custody/AGENTS.md) | 生产；唯一会下单的部分 |
| 核心 | **AH 股实时盯盘**：盯固定几只，港股走 OpenD、A 股走腾讯公开 K 线；只标注、不下单 | `watch/` | [watch/AGENTS.md](watch/AGENTS.md) | 生产（只读） |
| 研究切片 | 竞价强弱打分 `auction_strength`、港股通开盘窗口扫描 `hk_open_scan`、港股正股期权异动 `hk_near_expiry_flow`、盯盘信号研究 `watch_signal` | `studies/` | [studies/AGENTS.md](studies/AGENTS.md) | **不成熟**：原子能力或研究结论，不得当生产能力使用或对外承诺 |

共用底座（两项核心能力都依赖，规则见下）：OpenD 接入与安全边界、真实数据与 Release、研究方法。

## 安全与密钥

- 只有 `custody/broker.py` 可以使用交易接口；其他任何模块（含 `watch/`、`studies/`）不得引用 `OpenSecTradeContext`、`place_order`、`unlock_trade`、`modify_order`（测试扫描）。
- OpenD 登录密码、交易密码（`FUTU_TRADE_PASSWORD` / `FUTU_TRADE_PASSWORD_MD5`）、WxPusher SPT 等密钥只放环境变量或未跟踪的本地文件，绝不提交，也不作为命令行参数。

## 数据

- 只用真实行情；合成数据只用于单元测试。
- 数据不进 git。发布只走 GitHub Release（tag + zip + SHA256），发布后不可修改，新数据发新 tag；登记在 [docs/DATA.md](docs/DATA.md)。研究数据按用途分门别类发布。
- OpenD 历史 K 线额度（300 只 / 30 天）优先留给 custody 每日 `freeze`；研究只能重拉 30 天内已扣费的标的，要扣新额度须先征得用户同意。
- **任何后台、定时或长期运行的数据采集，未经用户同意不得启动**；写好脚本可以，运行由用户决定。

## 研究方法（全仓库通用）

- 动手前写预登记：假设、结构性理由、每轮 ≤ 5 个候选、选择规则与验证门槛；跑完不改规则。参数用整数或常见值，不做网格搜索。
- 训练 / 选择段与验证段分开；验证段只评测一次，绝不调参。看过结果才想到的改动，要换没用过的数据再验证。
- 不为单个 case 拟合；全部候选、结果和失败都记下来，不改写历史研究结果。
- 各能力的具体口径与门槛见其目录规范（custody 的门槛体系见 [docs/STANDARD.md](docs/STANDARD.md)）。

## 修改与验证

- 文档用中文；代码、标识符、提交信息用英文。运行时和评测只用标准库；`futu-api` 只在连 OpenD 的入口需要，研究脚本可用 numpy。
- 改哪个目录跑哪个目录的测试：
  - `python3 -m unittest discover -s custody/tests`（任何改动后都要跑，含本规范结构的检查）
  - `python3 watch/test_watch.py`
  - `python3 -m unittest discover -s studies/auction_strength/tests`、`python3 -m unittest discover -s studies/hk_open_scan/tests`
- 更新文档前核对代码、CLI 和已提交报告；不把约定写成尚未实现的自动保证。

## 文档索引

| 文档 | 内容 |
|---|---|
| [README.md](README.md) | 概览、离线快速开始 |
| [docs/DATA.md](docs/DATA.md) | 已登记数据集（custody 与研究）、哈希、冻结与发布 |
| [docs/STANDARD.md](docs/STANDARD.md) | custody：成交模型、对照组、指标、门槛与结论 |
| [docs/STRATEGY.md](docs/STRATEGY.md) | custody：当前策略、研究记录、修改步骤 |
| [docs/RUNTIME.md](docs/RUNTIME.md) | custody：状态机、dryrun、下单（run）、status / stop |
| [docs/OPEND_SETUP.md](docs/OPEND_SETUP.md) | OpenD 环境 |
| [watch/README.md](watch/README.md) | 盯盘用法、信号规则与实测 |
| [studies/watch_signal/README.md](studies/watch_signal/README.md) | 盯盘信号研究 W1–W11 |
