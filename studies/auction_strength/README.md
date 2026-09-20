# 竞价强度 × 当日走势（A 股 + 港股）

目标：在**竞价尚未最终撮合的最后一分钟**，用 OpenD 拉尽量全的快照/盘口，对 watchlist **打分排序**，省得人肉盯盘。

## 定时节奏（约定）

| 市场 | 触发 | 含义 |
|---|---|---|
| 港股 POS | **09:19** | 不可撤段末、随机撮合前约 1 分钟 |
| A 股 | **09:24** | 不可撤段末、09:25 定点撮合前约 1 分钟 |

由 Grok Bot 例程触发；执行机需本机 OpenD（默认 `127.0.0.1:11111`）且有对应行情权限。

## 强度怎么算（不是只看涨幅）

`live_score.py` 多因子（缺量会标 `data_grade=thin` 并**压分**）：

- 溢价 vs 昨收（权重有限）
- 量比 `volume_ratio`、成交额 / 流通市值
- 盘口失衡 `book_imbalance`（order book 或 L1 bid/ask vol）
- 竞价窗价格路径：尾盘脉冲惩罚、路径稳定性

OpenD 文档**没有**官方 IEP/IEV 字段名；竞价中 `last_price`/volume 语义以实盘为准，报告里会标 `data_grade`。

## Watchlist

- `config/watchlist_hk.json`
- `config/watchlist_a.json`

改名单即可，无需改代码。

## 命令

```bash
# 港股 9:19 脉冲（采样约 20s）
python3 studies/auction_strength/code/run_auction_pulse.py \
  --market HK \
  --watchlist studies/auction_strength/config/watchlist_hk.json \
  --duration 20 --out /tmp/auction_hk.json

# A 股 9:24
python3 studies/auction_strength/code/run_auction_pulse.py \
  --market A \
  --watchlist studies/auction_strength/config/watchlist_a.json \
  --duration 20 --out /tmp/auction_a.json

# 无 OpenD 的单测
python3 -m unittest discover -s studies/auction_strength/tests
```

## 历史 EOD 脚手架

`run_screen.py` + `fixtures/hk_2026-09-18.json` 仍可用作「开盘代理 × 全日走势」对照；**不能**替代 OpenD 竞价脉冲。
