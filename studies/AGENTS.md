# studies：研究切片（未达生产）

> 全仓库规则见 [../AGENTS.md](../AGENTS.md)。这里都是原子能力或研究结论，不当生产能力用、不对外承诺效果。

| 目录 | 内容 | 状态 |
|---|---|---|
| [auction_strength/](auction_strength/README.md) | 竞价最后一分钟强弱打分（港股 09:19、A 股 09:24） | 只有 3 天样本，门槛未校准 |
| [hk_open_scan/](hk_open_scan/README.md) | 港股通全市场竞价时点 / 09:45 排序 | 结论为负（追涨负期望） |
| [hk_near_expiry_flow/](hk_near_expiry_flow/README.md) | 港股正股期权近月异动扫描 | 结论为负（R1–R3 全部不达标） |
| [watch_signal/](watch_signal/README.md) | 盯盘信号研究 W1–W11 | 通过的结论已进 `watch/` |

- 每个切片自带 README（结论、数据、复现），预登记放 `notes/`，结果放 `reports/`。
- 切片升级为核心能力由用户决定：改根规范的能力地图，补该目录的 `AGENTS.md`。
