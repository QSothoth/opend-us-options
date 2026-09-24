# 盯盘信号研究（watch_signal）

给 `watch/smc_watch.py` 的标注做样本外检验与改进。只读行情，不下单，不进 `custody` 策略注册表。

## 一句话结论

**当前最佳方案**（2026-09-24，W1–W11 之后）：现有 `marks()` 标注照出；价位受限的带 `~`；日线背景 `D+`/`D-`；
只有「价位细且 `D+`」加粗。其余所有尝试（反转取反、资金流、价位反应、VWAP / 压缩 / 钉住日标记、日线偏向）都没通过。

以下是 W2 时的结论：

港股 1 分钟 K 线上，**「突破 / 缺口」这类顺势标注的对错，几乎完全取决于一根 K 线跨过几个价位（`tpb`）**：
不到 2 跳时它们系统性反向（样本外 t=−6.6），因为那里的「突破」多半是成交从买一翻到卖一；
2 跳以上就与抛硬币无显著差异，5 跳以上可能略正（样本外 t=+2.0）。盯盘已据此加了价位闸（W2；同日改为只打 `~` 标记，W9 后「5 跳以上加粗」也取消）。

在价位细的标的上，本轮试过的任何价量特征（多尺度动量 / 反转、相对恒指、VWAP 偏离、RSI、量比、RVOL、日内动量、时段）
**都不能把 30 分钟方向排序到显著优于 50%**。反转信号的表面优势（W1）是价位离散化的产物，以价位计只有 0.29 跳。

## 目录

| 文件 | 内容 |
|---|---|
| [notes/W1_PREREG.md](notes/W1_PREREG.md) / [reports/W1_RESULTS_CN.md](reports/W1_RESULTS_CN.md) | 相对恒指超涨超跌取反：过了门槛，事后诊断判无效，不采用 |
| [notes/W2_PREREG.md](notes/W2_PREREG.md) / [reports/W2_RESULTS_CN.md](reports/W2_RESULTS_CN.md) | 价位分层：样本外通过，盯盘里是 `~` 标记 |
| [reports/W3_RESULTS_CN.md](reports/W3_RESULTS_CN.md) | 价位细的标的上再提纯：无效 |
| [notes/W4_PREREG.md](notes/W4_PREREG.md) / [reports/FLOW_2026-09-24_CN.md](reports/FLOW_2026-09-24_CN.md) | 分钟资金流：单日测试无改善，搁置 |
| [notes/W5_PREREG.md](notes/W5_PREREG.md) / [reports/W5_RESULTS_CN.md](reports/W5_RESULTS_CN.md) | `~` 改看最近 N 根：未通过 |
| [notes/W6_PREREG.md](notes/W6_PREREG.md) / [reports/W6_RESULTS_CN.md](reports/W6_RESULTS_CN.md) | 日线背景 `D+`/`D-`：通过，已进盯盘 |
| [notes/W7_PREREG.md](notes/W7_PREREG.md) / [reports/W7_RESULTS_CN.md](reports/W7_RESULTS_CN.md) | 价位反应信号（30 分钟）：失败 |
| [notes/W8_PREREG.md](notes/W8_PREREG.md) / [reports/W8_RESULTS_CN.md](reports/W8_RESULTS_CN.md) | 不追 VWAP / 压缩 / 非钉住日：失败 |
| [notes/W9_PREREG.md](notes/W9_PREREG.md)、[W10](notes/W10_PREREG.md)、[W11](notes/W11_PREREG.md) / [reports/W9_W11_RESULTS_CN.md](reports/W9_W11_RESULTS_CN.md) | 全新 2025H2 留出段：价位反应到收盘失败；`D+` 复核成立（约 +11bp）；日线偏向失败 |
| [reports/DATA_SHA256.txt](reports/DATA_SHA256.txt) | 数据文件哈希（数据本身在 `data/`，不进 git） |
| `code/` | 研究脚本（numpy，只用于研究；盯盘运行时仍只用标准库） |

## 数据

已按用途发布为 6 个 GitHub Release（登记与哈希见 [docs/DATA.md](../../docs/DATA.md) 附录）：
`watch-hk1m-train-v1`（2025-12-01 → 2026-08-14）、`watch-hk1m-valid-v1`（08-17 → 09-23）、`watch-hk1m-holdout-2025h2-v1`（2025-06 → 11，第二留出段）、
`watch-hk-index1m-v1`（恒指等）、`watch-hk-daily-v1`（日 K，W6）、`watch-hk-flow-2026-09-24-partial`（资金流单日测试）。

全部来自本机 OpenD（只读），1m K 线**只拉 30 天内已扣费的标的**（`code/fetch_free.py` 遇到未扣费的直接拒绝），
历史 K 线额度始终 180 已用 / 120 剩余。研究脚本当时读的是 `data/` 下未拆分的原始文件（哈希见 [reports/DATA_SHA256.txt](reports/DATA_SHA256.txt)），
与 Release 是同一批行，只是 Release 按角色拆开了；`code/package_release.py` 是拆分脚本。

## 方法上的两条教训

1. **港股 1m 研究必须按 `tpb`（ATR / 一跳）分层报告。** 全样本汇总会被价位粗的标的主导（它们占训练集 80% 的标的-日），
   得出的「追势系统性反向」「反转有效」对智谱、MiniMax 这类价位细的标的都不成立。
2. **用延后入场检验衰减。** 同一根收盘价入场会吃到买卖价反弹；主口径用「下一根收盘入场」，并看延后 5、10 根还剩多少。

## 进展

| 轮次 | 结论 |
|---|---|
| W1 | 相对恒指超涨超跌取反：过门槛但是价位离散化产物，不采用 |
| W2 | 价位分层：样本外通过；先做成隐藏、同日改为降级显示 `~` |
| W3 | 价位细的标的上用 1m 价量特征再提纯保留标注：无效（未动用验证集） |
| W5 | `~` 改用最近 N 根的价位密度：未通过；且验证段里原口径的区分度也消失 |
| W6 | **日线背景 K5（逆近 5 日 + 未破昨日高低点）：验证通过，盯盘加 `D+`/`D-`**；三段复核后可信量级约 +11bp（t=2.0），加粗改给「价位细且 `D+`」 |
| W7 | 价位反应信号（昨日高低 / 开盘区间的拒绝与失败突破）：30 分钟口径失败；到收盘两段为正，转 W9 用全新数据检验 |
| W8 | 不追 VWAP / 压缩突破 / 非钉住日三个标记：都没过选择段 |
| W9 | 价位反应信号按到收盘、在全新 2025-06 → 11 上验证：失败（+0.2bp）；同段复核 W6：`D+` 方向成立、未参与挑选段差 +11.5bp（t=2.0） |
| W10 | 组合方案（只报告）：加入价位反应只会稀释 `D+` |
| W11 | 只按「逆近 5 日」定当天方向：失败；`D+` 是盘中触发 × 日线背景的交互 |
| W4 | 分钟资金流：单日测试无改善，用户判断无意义，**搁置**；盯盘里的 `f` 已撤下。见 [reports/FLOW_2026-09-24_CN.md](reports/FLOW_2026-09-24_CN.md) |

采集脚本已写好但**没有运行、没有定时任务**；用户同意后才手动或定时跑（收盘后一次）：

```bash
cd studies/watch_signal/code && /opt/futu-opend/venv/bin/python capture_flow.py --out ../data/flow
```
