# 港股近月正股期权流量研究（HK near-expiry equity options）

本目录是挂在 `s-alpha` 下的**研究切片**，与美股 0DTE custody 主线并列，不另开仓库。  
研究对象是港股**上市正股期权**（如 `HK.MNX260918C235000`），**不是涡轮**。

## 目录

| 路径 | 内容 |
|---|---|
| `code/` | 只读扫描器 `scan_hk_option_flow.py`（默认 `--source rank`，需 OpenD）；离线研究台 `flow_research.py`（只用标准库 + 已冻结 CSV） |
| `dataset/` | 已拉取的排行 / 异动 / 日 K 等 CSV + `manifest.json` |
| `reports/` | 中文回测与审计笔记 |
| [DATA_REQUEST.md](DATA_REQUEST.md) | **要补的数据规格**：拉什么、怎么存、补多少算够 |
| `notes/` | 预注册（`R*_PREREG.md`）：假设、候选、判定规则，跑数之前写死 |

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

## 离线复现（不需要 OpenD）

```bash
python3 code/flow_research.py --diagnostics --csv reports/R1_R3_results.csv
```

只读 `dataset/` 里已冻结的 CSV，纯标准库。母体 = 近月 CALL 有排行成交且正股有日 K 的
**404 个标的日 / 30 天 / 40 个标的**；收益一律做**大盘调整**（同日 K 线面板横截面均值），
显著性用**同日内置换检验**。

## 已观察到的限制（给后续筛选用）

1. 排行历史大约只能可靠回到 **2026-08-07**；rank 只有 **CALL**，每日成交额前 400 条。  
2. 异动接口不适合做长区间近月回测。  
3. **现行 aggressive 门槛不是掷硬币，是显著的负向筛子**：扣掉大盘后 T+2 超额 −1.03%
   （母体 −0.39%，同日置换 p=0.013）。按绝对成交额筛近月 CALL 选出的是大市值 + 高 beta。  
4. 旧报告的「T+1～T+3 冲高胜率 54%～58%」是**基准率**：面板里任意个股任意一天，3 日内
   高于前收的概率就是 87%，有没有期权流量没差别。  
5. R1～R3 共 13 个候选（相对放量、V/OI、彩票档、集中度、IV 抬升、新合约、末日占比、
   delta+theta 残差……）**全部不达标**，明细见 [reports/R1_R3_RESULTS.md](reports/R1_R3_RESULTS.md)。  
6. MiniMax 9/16 成交额最大的 `260918C235` 当天以 **减仓** 为主；9/15 同合约是增仓。
   多个候选的正超额在**剔除 MiniMax 后归零**——不要为这一票调参。

下一步是收数据不是调参：每日冻结 rank 的 **CALL + PUT** 全量与全部 owner 的正股日 K
（rank 历史只有约 6 周，不冻就补不回来），攒够样本后把 R3 的 E1/E2/E3 当样本外检验。
