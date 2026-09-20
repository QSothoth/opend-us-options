# 港股近月正股期权流量研究（HK near-expiry equity options）

本目录是挂在 `s-alpha` 下的**研究切片**，与美股 0DTE custody 主线并列，不另开仓库。  
研究对象是港股**上市正股期权**（如 `HK.MNX260918C235000`），**不是涡轮**。

## 目录

| 路径 | 内容 |
|---|---|
| `code/` | 只读扫描器 `scan_hk_option_flow.py`（默认 `--source rank`） |
| `dataset/` | 已拉取的排行 / 异动 / 日 K 等 CSV + `manifest.json` |
| `reports/` | 中文回测与审计笔记 |
| `notes/` | 后续你自己筛法可写在这里 |

## 数据怎么用

先看 `dataset/manifest.json`。核心表：

- `hk_option_rank_raw.csv`：按交易日校验过的 `get_option_rank` 原始行（含 `oi_increment` / `oi_decrement`、本地算的 `dte`、`owner`）
- `hk_option_alerts.csv`：一版宽网「标的日」报警及 `ret_1d` / `ret_2d`
- `hk_call_buy_365d_raw.csv` 等：异动接口历史（**仍挂牌合约偏差**，过期近月会被清掉）

复现扫描（需本机 OpenD `127.0.0.1:11111`）：

```bash
python3 code/scan_hk_option_flow.py --backtest --source rank --preset aggressive \
  --from 2026-08-07 --to 2026-09-18 --out-dir /tmp/hk-flow-out
```

## 已观察到的限制（给后续筛选用）

1. 排行历史大约只能可靠回到 **2026-08-07**。  
2. 异动接口不适合做长区间近月回测。  
3. 当前门槛下 T+1/T+2/T+3 **收盘胜率接近掷硬币**，尚未形成可宣称的边。  
4. MiniMax 9/16 成交额最大的 `260918C235` 当天以 **减仓** 为主；9/15 同合约是增仓。

筛选逻辑请你在本地或本目录 `notes/` 继续迭代；本包只负责把原始材料放进总仓库。
