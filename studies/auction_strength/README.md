# 竞价强度 × 当日走势筛选（A 股 + 港股）

挂在 `s-alpha` 下的研究切片，与美股 0DTE `custody` 主线并列。目标：在**开市竞价阶段**找出相对强的标的，并与**当日实际走势质量**对齐，供后续盯盘筛股（先历史验证，再接 OpenD 实盘只读）。

## 验证集（2026-09-18 港股）

| 标的 | 代码 | 开盘≈竞价 | 收盘 | 标签倾向 |
|---|---|---|---|---|
| MiniMax | `HK.00100` | +3.92% | +18.92% 收最高 | **gap-and-go**（典型正例） |
| 智谱 | `HK.02513` | +3.58% | +5.41% | 高开回踩再走强 |
| 阿里 | `HK.09988` | +1.24% | +4.00% | 竞价偏弱、全日温和偏强 |

公开渠道未拿到三只当日 IEP/IEV，fixture 用**开盘价相对昨收**作竞价代理（`auction_field=open_proxy`）。有 OpenD 后应写入真实 `auction_price` / `auction_volume`。

默认门槛（可改）：竞价 ≥ **+1.5%**、全日 ≥ **+2.5%**、跟随（收−开）/昨收 ≥ 0、综合分 ≥ 55。  
在该门槛下：**选出 MiniMax、智谱；阿里因竞价不足落选**。

## 市场时序（同为 UTC+8）

| | 港股 POS | A 股集合竞价 |
|---|---|---|
| 可撤单 | 09:00–09:15 | 09:15–09:20 |
| 不可撤单 | 09:15–09:20 | 09:20–09:25 |
| 撮合 | 09:20–≤09:22 随机 IEP | **09:25 定点** |
| 连续竞价 | 09:30 | 09:30 |

港股竞价信号更早；跨市盯盘不要套用 A 股 9:25 逻辑。

## 怎么跑

```bash
# 仓库根目录
python3 studies/auction_strength/code/run_screen.py \
  --fixture studies/auction_strength/fixtures/hk_2026-09-18.json

python3 -m unittest discover -s studies/auction_strength/tests
```

## 指标（简版）

- `auction_pct`：竞价价（或开盘代理）相对昨收  
- `day_pct`：收盘相对昨收  
- `follow_through_pct`：(收−开)/昨收  
- `close_location`：收盘在当日高低区间位置（1=最高）  
- `combined_score`：竞价强度与走势质量加权  

## 下一步（盯盘）

1. OpenD 只读拉取竞价快照（A：09:20–09:25；港：09:15–09:22）写入同 schema。  
2. 盘中把「竞价入选」与分钟走势再打分（回撤、是否跌破开盘）。  
3. A 股 watchlist（如云从/汉得等）用同一 CLI，换 fixture 或 live adapter。  

本包**不下单**；`custody/broker.py` 交易边界不变。
