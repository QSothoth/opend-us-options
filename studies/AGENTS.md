# studies 规范：研究切片（未达生产）

> 先读根目录 [AGENTS.md](../AGENTS.md)。这里的东西**都不是成熟能力**：是原子能力或研究结论，不得当生产能力使用、不得对外承诺效果。

## 现有切片

| 目录 | 内容 | 状态 |
|---|---|---|
| [auction_strength/](auction_strength/README.md) | 竞价最后一分钟的强弱打分（港股 09:19、A 股 09:24） | 原子能力；只有 3 天样本，门槛未做样本外校准 |
| [hk_open_scan/](hk_open_scan/README.md) | 港股通全市场在竞价时点与 09:45 的横截面排序 | 研究结论为负（追涨负期望） |
| [hk_near_expiry_flow/](hk_near_expiry_flow/README.md) | 港股正股期权近月异动扫描 | 研究结论为负（R1–R3 候选全部不达标） |
| [watch_signal/](watch_signal/README.md) | 盯盘信号研究 W1–W11 | 为 `watch/` 提供依据；通过的结论已进盯盘 |

## 规则

- 只读行情，不下单（交易接口只在 `custody/broker.py`）。
- 每个切片自带 README（结论、数据、复现方式），预登记放 `notes/`，结果放 `reports/`；失败和否定结果照样写。
- 研究数据不进 git，按用途分门别类发 GitHub Release 并登记到 [docs/DATA.md](../docs/DATA.md)。
- 历史 K 线额度与数据采集的限制见根规范「数据」：只重拉已扣费标的；后台、定时或长期采集须用户先同意。
- 切片要升级为核心能力，需要用户决定，并在根规范的能力地图里改状态、补该能力目录的 `AGENTS.md`。
- 改了哪个切片就跑哪个切片的测试（命令见根规范）。
