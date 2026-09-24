# 项目规范

> 先读本文件，再读要改的目录的 `AGENTS.md`；细节在 `docs/` 和各目录 README，不抄回规范。每个 `CLAUDE.md` 只有一行 `@AGENTS.md`（测试强制）。

## 能力地图

| 层级 | 能力 | 规范 |
|---|---|---|
| 核心 | 美股期权日内单笔交易（主要是 0DTE）：上游给定标的、方向、合约，本仓库只做时机；唯一会下单的部分 | [custody/AGENTS.md](custody/AGENTS.md) |
| 核心 | AH 股实时盯盘：只读、只标注 | [watch/AGENTS.md](watch/AGENTS.md) |
| 研究切片 | 竞价打分、开盘扫描、期权异动、盯盘信号研究——**不成熟，不当生产能力用** | [studies/AGENTS.md](studies/AGENTS.md) |

## 安全

- 交易接口（`OpenSecTradeContext`、`place_order`、`unlock_trade`、`modify_order`）只允许 `custody/broker.py` 使用（测试扫描全仓库）。
- 密码、交易密码（`FUTU_TRADE_PASSWORD` / `FUTU_TRADE_PASSWORD_MD5`）、WxPusher SPT 等只放环境变量或未跟踪文件，不提交、不作命令行参数。

## 数据

- 只用真实行情；合成数据只用于单元测试。
- 数据不进 git，只发 GitHub Release（tag + zip + SHA256），发布后不改、新数据发新 tag，按用途分类登记在 [docs/DATA.md](docs/DATA.md)。
- 历史 K 线额度优先留给 custody 每日 `freeze`；研究只重拉已扣费标的，扣新额度要先问用户。
- 后台、定时或长期的数据采集，未经用户同意不启动。

## 研究方法

- 先预登记：假设、结构性理由、每轮 ≤ 5 个候选、选择规则与门槛；跑完不改。参数取整数或常见值，不做网格搜索。
- 选择段与验证段分开，验证段只评测一次、不调参；看完结果才想到的改动，换没用过的数据再验。
- 不为单个 case 拟合；失败照记，不改写历史结果。

## 修改与验证

- 文档中文；代码、标识符、提交信息英文。运行时只用标准库（连 OpenD 的入口需 `futu-api`；研究脚本可用 numpy）。
- 改哪里跑哪里的测试，`custody/tests` 每次都跑（含规范结构检查）：
  `python3 -m unittest discover -s custody/tests` · `python3 watch/test_watch.py` ·
  `python3 -m unittest discover -s studies/auction_strength/tests` · `python3 -m unittest discover -s studies/hk_open_scan/tests`
- 改文档前核对代码、CLI 和已提交报告；不把约定写成尚未实现的自动保证。
- 其余文档：[README.md](README.md)（概览）、[docs/OPEND_SETUP.md](docs/OPEND_SETUP.md)（OpenD 环境）。
