# 竞价强度脉冲（OpenD T-1）

在**竞价尚未最终撮合的最后一分钟**，用本机 OpenD 拉 snapshot + order book，对 watchlist **多因子打分排序**，输出强弱。与 `custody` 主线独立：只读行情、不下单。

## 定时约定

| 市场 | 触发（Asia/Shanghai） | 含义 |
|---|---|---|
| 港股 POS | **工作日 09:19** | 不可撤段末、随机撮合前约 1 分钟 |
| A 股 | **工作日 09:24** | 不可撤段末、09:25 定点撮合前约 1 分钟 |

由 **Grok Bot 例程**触发。例程必须在**装有 OpenD 的那台机器上本地执行**（默认 `127.0.0.1:11111`），远程 CI / 无 OpenD 的沙箱只能跑 fixture 单测，不能当实盘脉冲。

## 强度怎么算（不是只看开盘涨幅）

`live_score.py` 多因子；缺量标 `data_grade=thin` 并**压分**（thin 上限 40，partial 上限 75）：

| 因子 | 字段 | 说明 |
|---|---|---|
| 溢价 | `premium_pct` | 现价 vs 昨收，权重有限 |
| 价路径 | `path_late_spike` / `path_stability` | 尾盘脉冲惩罚、路径稳定性 |
| 量 / 额 | `volume` / `volume_ratio` / `turnover` / `float_turnover` | 量比、成交额 / 流通市值 |
| 委比 / 盘口 | `book_imbalance` / `bid_ask_ratio` | order book 或 L1 买卖量失衡 |

OpenD 文档没有官方 IEP/IEV 字段名；竞价中 `last_price` / volume 语义以实盘为准，报告里会标 `data_grade`（`full` / `partial` / `thin`）。

表格和 JSON **都包含** `rank`、`score`、`label`、`data_grade` 和 `factors`。

## Watchlist 格式

默认名单：

- `config/watchlist_hk.json`
- `config/watchlist_a.json`

JSON 形状：

```json
{
  "market": "HK",
  "note": "可随时改名单，不必改代码",
  "symbols": [
    {"code": "HK.00100", "name": "MiniMax"},
    "HK.09988"
  ]
}
```

`symbols` 可以是 `{"code","name"}` 对象，或纯代码字符串。CLI 可用 `--codes` 做临时名单或从 watchlist 里筛选；也支持无前缀写法（`00100` → `HK.00100`，`688327` → `SH.688327`）。

## 无 OpenD：fixture 与单测

不连 OpenD、不装 `futu-api` 也能跑：

```bash
# 研究切片单测（fake quote context + fixture）
python3 -m unittest discover -s studies/auction_strength/tests

# 用已保存的 capture 打分（不碰 OpenD）
python3 studies/auction_strength/code/run_auction_pulse.py \
  --market HK \
  --from-capture studies/auction_strength/fixtures/live_capture_hk.json
```

| 文件 | 用途 |
|---|---|
| `fixtures/live_capture_hk.json` | 模拟竞价窗 snapshot 序列，测 live 打分 / CLI |
| `fixtures/hk_2026-09-18.json` | 日终开盘代理 × 全日走势对照（不能替代脉冲） |
| `fixtures/a_share_schema_example.json` | A 股日终脚手架字段示例 |

## 实盘脉冲命令

本机 OpenD 已登录且有对应市场实时行情权限时：

```bash
# 港股 9:19（默认读 config/watchlist_hk.json，采样约 20s）
python3 studies/auction_strength/code/run_auction_pulse.py \
  --market HK \
  --duration 20 --out /tmp/auction_hk.json

# A 股 9:24
python3 studies/auction_strength/code/run_auction_pulse.py \
  --market A \
  --watchlist studies/auction_strength/config/watchlist_a.json \
  --duration 20 --out /tmp/auction_a.json

# 临时名单（不改仓库 watchlist）
python3 studies/auction_strength/code/run_auction_pulse.py \
  --market HK --codes HK.00100,HK.09988,HK.01810

# 从默认 watchlist 筛选
python3 studies/auction_strength/code/run_auction_pulse.py \
  --market HK \
  --watchlist studies/auction_strength/config/watchlist_hk.json \
  --codes 00100,02513
```

一次脉冲窗口内复用**同一条** `OpenQuoteContext`：订阅一次，循环 snapshot / order book，结束才 close。默认 host/port：`127.0.0.1:11111`（`--host` / `--port` 可改）。

stdout 先打表格，再打 JSON（`rows[]` 含 rank / score / label / data_grade / factors）。`--out` 另存完整 capture（含 `series`）。

## 例程对接前提

Grok Bot 要把这条例程跑起来，需要同时满足：

1. **本机 OpenD** 已启动并登录，监听 `127.0.0.1:11111`（或例程传入的 host/port）。
2. **Local execution**：例程在 OpenD 同一台机器上执行；不要丢到没有 OpenD 的远程 runner。
3. OpenD 账号有该市场的**实时行情权限**（港股 / A 股分开）。
4. Python 侧 `pip install futu-api`（仅实盘脉冲需要；单测不需要）。
5. 触发时刻：港股工作日 09:19、A 股工作日 09:24，时区 `Asia/Shanghai`。
6. 只读：只用 `OpenQuoteContext`。此目录禁止交易接口、解锁、下单。

## 只读约束

`opend_auction.py` / `run_auction_pulse.py` 只连行情上下文。密钥不进 git、不进命令行（见 [docs/OPEND_SETUP.md](../../docs/OPEND_SETUP.md)）。

## 历史 EOD 脚手架

`run_screen.py` + `fixtures/hk_2026-09-18.json` 仍可用作「开盘代理 × 全日走势」对照；**不能**替代 OpenD 竞价脉冲。
