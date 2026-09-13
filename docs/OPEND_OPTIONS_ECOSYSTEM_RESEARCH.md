# 研究报告：基于富途 OpenD 的期权程序化交易生态

> 目标：调研是否有其他人在 Futu OpenD（futu-api / moomoo-api）上做**期权**的程序化交易 / 扫描器 / 回测，并为 `lowbuy-dip-scout` 的 `scripts/opend_us_options` 提取可复用的工程模式。
>
> 方法：以 GitHub 仓库/README/源码证据为主，官方 OpenAPI 文档为辅；不采信营销文案。已区分「纯股票 OpenD 机器人」与「**期权**专用」。
>
> 合规声明：本次调研**未下单、未连接本机实时 OpenD 实例**；所有结论来自公开仓库与官方文档的离线阅读。
>
> 调研日期：2026-09-13（依据环境内文件时间）。

---

## 1. 结论摘要：OpenD 期权自动化有多普遍？

一句话：**「股票/正股」的 OpenD 自动化生态已经成熟，但「期权」专用工具生态非常薄——有能力的 SDK、没有成气候的开源工具。**

- **官方 SDK 能力是完整的**：`futu-api`（香港）/ `moomoo-api`（美国，同一协议、同版本 10.10.7008）原生支持期权行情与交易：
  - `get_option_expiration_date`（取到期日）
  - `get_option_chain`（按标的 + 到期日区间 + 价内价外 + Greeks/成交量/OI 过滤拉期权链）
  - `get_market_snapshot` / `get_stock_quote`（含 `option_delta/gamma/vega/theta/implied_volatility/open_interest` 等希腊值与 IV）
  - `OpenSecTradeContext.place_order`（期权按「张」下单）
- **但社区里「期权专用」的开源项目数量少、星标低、大多未维护或只读**。本次在 GitHub 上能定位到的、真正做期权（而非股票/期货）的项目约 8–10 个，星标基本都在 **0–2 星**，其中多个明确「只读/纸面/回测」，或处于「Phase 1 未完成」状态。
- 对照之下，**股票类** OpenD 项目要成熟得多（futu_algo 590★、WallTrading-Bot 117★、OpenQuant 51★、bt-futu-store、nautilus-futu、各种 MCP server 等），但它们**不做期权**。
- **一个高度一致的社区共识**：OpenD 用于「实时」期权行情/持仓/下单是可行的；但**回测所需的历史期权链/历史希腊值，OpenD 拿不到**，大家都转用外部数据源（yfinance / Tradier / CBOE DataShop / ThetaData / Databento / Polygon 系 Massive / IBKR / Alpaca）或用 Black-Scholes 合成价格。

**诚实结论**：OpenD 期权自动化不是没人做，而是「各自为战、极少公开、缺少维护与测试」；可以直接抄的成熟期权框架基本没有，能抄的是**工程模式**（缓存、限频、回测防前视），而不是策略逻辑。

---

## 2. 值得关注的仓库 / 文档

星标数为调研时点（2026-09）通过 GitHub 搜索结果读取的数值。

### 2.1 官方 SDK 与文档（能力基线）

| 仓库 | 星 | 说明 |
|---|---|---|
| [FutunnOpen/py-futu-api](https://github.com/FutunnOpen/py-futu-api) | 1.3k | 官方 Python SDK。期权相关：`get_option_chain`、`get_option_expiration_date`、`get_market_snapshot`（含期权希腊值字段）、交易上下文（期权按张下单）。README 即快速上手。 |
| [FutunnOpen/futu-api-doc](https://github.com/FutunnOpen/futu-api-doc) | 77 | 官方接口文档源（RST）。**限频/额度数字的权威出处**（见 §3.2）。 |
| [MoomooOpen/py-moomoo-api](https://github.com/MoomooOpen/py-moomoo-api) | 44 | 美国版 SDK，协议与 `futu-api` 相同（PyPI 同为 10.10.7008）。美国账户用 `moomoo` 包名 + `SecurityFirm.FUTUINC`。 |
| [FutunnOpen/futu-agent-hub](https://github.com/FutunnOpen/futu-agent-hub) | 38 | 官方 AI 技能包（趋势：把 OpenD 能力封装成 Agent/MCP skill）。 |

### 2.2 股票类（用于对照，**非期权**）

| 仓库 | 星 | 说明 |
|---|---|---|
| [billpwchan/futu_algo](https://github.com/billpwchan/futu_algo) | 590 | 最成熟的 OpenD 股票量化框架：历史 K 线下载到 CSV/SQLite、Pyfolio 回测、实时交易、选股器。**仅港股正股，无期权**。 |
| [lookatwallstreet/WallTrading-Bot-MooMoo-Futu](https://github.com/lookatwallstreet/WallTrading-Bot-MooMoo-Futu) | 117 | 股票交易框架（策略模板 + 下单 + 账户）。无期权。 |
| [luwening/OpenQuant](https://github.com/luwening/OpenQuant) | 51 | 富途量化平台（C++）。 |
| [damonYuan/bt-futu-store](https://github.com/damonYuan/bt-futu-store) | 22 | Backtrader ↔ 富途的数据/交易 store（正股）。 |
| [loadstarCN/nautilus-futu](https://github.com/loadstarCN/nautilus-futu) | 11 | NautilusTrader 的 OpenD 适配器（Rust+Python）。 |
| [xyonium/futu-opend-mcp](https://github.com/xyonium/futu-opend-mcp) | 9 | 只读行情 MCP server（明确不包含下单）。 |
| [Litash/moomoo-api-mcp](https://github.com/Litash/moomoo-api-mcp) | 29 | moomoo OpenAPI 的 MCP server。 |

> 上面这些的「只读/下单」边界与模块拆分值得借鉴，但**策略与期权无关**。

### 2.3 期权专用（本次调研的核心发现，均低星、需谨慎）

| 仓库 | 星 | 只读还是交易 | 市场 | 实际做什么 |
|---|---|---|---|---|
| [Easonluoyuchen/option_trigger](https://github.com/Easonluoyuchen/option_trigger) | 1 | **交易**（自动平仓） | 港/美（长桥+富途） | 期权止盈/止损/移动止损/Delta 平仓/时间平仓；WebUI 轮询；含 `dry_run`、每日交易次数、亏损上限、持仓数限制等安全开关。`core/futu_broker.py` 展示了：`get_option_chain` → `subscribe` → `get_stock_quote`（拿 delta/gamma/vega/theta/IV）→ `place_order` 平仓。**可抄其安全开关设计，但别抄交易。** |
| [reno77/0dte-options-bot](https://github.com/reno77/0dte-options-bot) | 2 | 纸面/实盘（未完成） | 美（SPX/XSP/SPY/QQQ/NDX） | 0DTE 铁鹰/ORB/信用价差；`backtest/` 用 yfinance 标的 + Black-Scholes 合成期权价 + IV 偏斜拟合做回测；`execution/moomoo_gateway.py` 用 moomoo OpenD 下单。README 显示 Phase 1–3 仍未完成。**其 `data_fetcher.py` 明说「真实期权 tick 数据要升级 ThetaData/Databento/CBOE」——直接佐证 OpenD 无历史期权数据。** |
| [cowboysipke/options-radar-nas](https://github.com/cowboysipke/options-radar-nas) | 0 | **只读**（交易锁定）+ 纸面 | 美 | 飞牛 NAS 异常期权助手：Discord 信号 → DeepSeek 解析 → 富途 OpenD 实时验证（期权链/快照/持仓/自选）→ 中文日报/飞书；模拟盘 + 1/3/5 日回放回测。历史数据走 **Massive / IBKR / Alpaca**（`history_adapters.py`），OpenD 只做实时。SQLite 缓存（`option_snapshots`/`portfolio_snapshots` 表）见 §3.1。**这是「OpenD 实时 + 外部历史」这一标准架构的最好范本。** |
| [incognitoCodes/trading-ai-options](https://github.com/incognitoCodes/trading-ai-options) | 1 | 交易（默认纸面） | 美 | moomoo 期权交易 CLI + 研究 agents：垂直价差/铁鹰/铁蝶/裸单、payoff+Greeks 仿真；`options_backtester.py` 回测卖 put 信用价差，**明说「免费历史期权链不存在，用 BS + 真实 VIX 定价」**。`config.py` 有 `API_SLEEP_SECONDS=0.1` 限频。 |
| [ta-ke-o/mooquant](https://github.com/ta-ke-o/mooquant) | 1 | 分析平台 | 美 | local-first 分析与自动化平台（偏股票）。 |
| [wabzqem/bullcrab](https://github.com/wabzqem/bullcrab) | 0 | 只读 | 美 | Rust MCP server，经 moomoo OpenD 提供实时持仓/股票/期权数据。 |
| [iocfinc/cuddly-lunatic-system](https://github.com/iocfinc/cuddly-lunatic-system) | 0 | 只读/研究 | 美 | Agent 化期权研究台，moomoo OpenD + Telegram 报告 + 波动率分析。 |
| [stevencwt/moomoo](https://github.com/stevencwt/moomoo) | 0 | 交易 | 美 | 期权交易（内容极薄，仅能确认存在此类尝试）。 |

### 2.4 博客 / 文档

- **权威**：官方文档 [openapi.futunn.com/futu-api-doc](https://openapi.futunn.com/futu-api-doc/)（限频、权限、期权字段、下单规则都在这里）。README 之外的细节（如 `node.xml` 配置、OpenD 命令行参数、行情互踢）见其「FutuOpenDGuide」。
- **博客/知乎/CSDN**：存在大量「富途 OpenD 入门/期权链」教程，但多为 SDK 调用搬运或营销，**无可引用的权威博客**。本报告不采信这类来源，证据以仓库源码 + 官方文档为准。

---

## 3. 值得抄的模式（给 `scripts/opend_us_options`）

### 3.1 本地行情缓存

- **`options-radar-nas` 的 SQLite 快照表**（最值得抄）：
  ```sql
  CREATE TABLE option_snapshots(
    id INTEGER PRIMARY KEY,
    contract_key TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
  );
  -- 另有 portfolio_snapshots(observed_at, symbol, payload_json)
  ```
  要点：追加式（append-only）时间序列、`contract_key = symbol|expiry|strike|type`、原始 payload 落 JSON、`PRAGMA journal_mode=WAL`。任何「合约级快照」都能复用这套 schema。
- **`futu_algo`**：历史 K 线「下载到 CSV → 入库 SQLite → 回测只读库」，`load_or_fetch` + `--no-fetch` 的 cache-first 模式（`0dte-options-bot/backtest_engine.py` 同款）。
- **我们自己的现状**：`/home/box/futu/snapshots/YYYY-MM-DD.json` 已经是「按日账户+持仓快照」的雏形；`aapl200_state.json` 是「计划/状态/订单/标记」的状态缓存。新模块应把它们统一进一个带 schema 的 SQLite/Parquet 缓存，而不是继续散落 JSON。
- **建议**：分三类缓存并设置不同 TTL——
  1. 静态（期权链 `get_option_chain` 返回的 code/strike/expiry/乘数）：按到期日缓存，日级更新；
  2. 准实时（`get_market_snapshot` 的 bid/ask/last/greeks）：RTH 内 5–15s，盘外拉长；
  3. 交易态（持仓/账户）：由交易事件触发刷新，并保留 `refresh_cache=False` 默认（OpenD 自带缓存）。

### 3.2 限频 / 额度保护（官方数字，直接照搬）

来自官方文档 `protocol/intro.rst`（权威）：

| 接口 | 官方限制 |
|---|---|
| `get_option_chain`（获取期权链） | **30 秒内 ≤ 10 次**；到期日时间跨度 **≤ 30 天** |
| `get_market_snapshot`（快照） | **30 秒内 ≤ 60 次**；单次 ≤ **400** 个代码；港股期权 BMP 权限单次 ≤ 20 个 |
| `subscribe/unsubscribe`（订阅） | 每只股票每个 SubType 占 **1 个订阅额度**（额度由账户总资产/交易量分级）；订阅至少 1 分钟才能反订阅 |
| `request_history_kline`（在线历史 K 线） | **30 秒内 ≤ 60 次**；分钟级近 2 年、日级近 10 年；30 天内可请求股票数受额度限制（用 `get_history_kl_quota` 查询已用/剩余） |
| `get_cur_kline` | 最多最近 1000 根 |
| `place_order` / `modify_order` | 30 秒内 ≤ 15 / 20 次，间隔 ≥ 0.02s |

社区做法：
- `trading-ai-options/config.py`：`API_SLEEP_SECONDS = 0.1`（自注「约 15 次/30 秒」），每次拉链前 sleep；
- `0dte-options-bot/moomoo_gateway.py`：每个到期日之间 `time.sleep(0.3)`，且 `expirations[:5]` 主动截断；
- `option_trigger/futu_broker.py`：`subscribe → sleep(0.5) → get_stock_quote`（订阅后稍等再取）；
- `options-radar-nas/backup_providers.py`：**滑窗配额器**（`quota_window_seconds=60`、`_trim_calls()` 滑窗计数、超限抛 `ProviderRateLimitError`、`cache_seconds` TTL 缓存）——这是一个可直接照抄的通用「限频+缓存」实现。

**建议**：为 `opend_us_options` 写一个中央 `RateLimiter`（滑动窗口，按协议分别配 10/30s、60/30s 等上限）；`get_market_snapshot` 永远按 ≤400 一批（现有 `aapl_calls_scan.py` 已按 80 一批）；批量历史 K 线前先 `get_history_kl_quota`。

### 3.3 期权回测防前视（look-ahead bias）

因为 OpenD **没有历史期权链/历史希腊值**，社区只有两条路，都必须显式防前视：

1. **合成定价**（`trading-ai-options`、`0dte-options-bot`）：用 Black-Scholes + 当日 VIX/偏斜给期权合成价格。
   - 必须只用「决策时点之前」的数据估计 IV/希腊值（如 `trading-ai-options` 用 rolling 252 日 IV 分位、rolling 200 SMA）。
   - 诚实记录假设（它自述「看不到周末 IV 跳空、提前行权、真实偏斜」）。
   - 对成交价做滑点/价差折扣（`SLIPPAGE_PCT`）。
   - 用 in/out-of-sample 切分验证。
2. **买/接外部历史数据**（`options-radar-nas` 用 Massive/IBKR/Alpaca；`0dte` 建议 ThetaData/Databento/CBOE）。

**`options-radar-nas/analyst_backtest.py` 的时点纪律（最值得抄）**：
- 信号日 `T` → `entry_day = add_business_days(T, 1)`，**次一交易日才进场**；
- 股票进场价用 `bars[1].open`（次根 bar 开盘），而不是信号日的收盘；
- 所有序列严格 `[:horizon]` 向后切片，绝不使用 entry 时点之后的数据做入场决策。

**建议（落到我们的实现）**：
- 每笔行情/快照都带 `observed_at`（市场时间）与 `decided_at`（策略时间），回测 join 时强制 `bar.time <= decided_at`；
- 「信号用收盘价、成交用下一根开盘价」作为默认规则；
- 期权成交用 `(bid+ask)/2` 中价并扣滑点，**过滤 `bid<=0 or ask<=0`**（现有 `aapl_calls_scan.py` 已这么做）。

---

## 4. 风险与缺口

1. **没有历史期权链 / 历史希腊值**（最大缺口）。`get_option_chain` 返回的是「当前」链 + 静态字段（code/行权价/到期/乘数/结算方式等），**无 `as-of` 历史参数**；价格与希腊值只能靠 `get_market_snapshot` 实时拿。回测必须外购数据或合成——两者都会引入偏差。
2. **期权链限频紧**：10 次/30 秒 + 单次跨度 30 天。多标的 × 多到期日扫描会很慢，必须配合缓存 + 限频器（这正是 §3.2 缓存层优先的原因）。
3. **历史 K 线**：分钟级仅 2 年、日级 10 年，且文档按「股票」描述；**已退市/已到期期权的历史 K 线是否可取未明确**，不能假设能拉到期权全生命周期历史。
4. **订阅额度与权限**：订阅按「股 × 类型」计额度，额度随账户资产/交易量分级；港股期权 LV1 不支持订阅逐笔；港股 BMP 下期权快照单次 20 个上限。
5. **行情互踢**：同一账号多终端只能有一个持有最高行情权限（`auto_hold_quote_right` 可自动抢回）。挂机场景要留意。
6. **OpenD 依赖与登录态**：OpenD 必须常驻且登录有效（UI 版 token 过期可能断连；命令行版更稳）。`options-radar-nas/opend_manager.py` 甚至管理 OpenD 进程 + 固定版本 manifest——说明这是长期运行的痛处。
7. **交易侧**：下单前需 `unlock_trade` 解锁；实盘有下单/改单限频；期权按「张」计价；美国期权代码格式 `US.{标的}{YYMMDD}{C|P}{行权价含3位小数}` 易错；指数期权 `index_option_type` 需 NORMAL/SMALL/NONE 试错（`option_trigger` 源码可见）。
8. **生态成熟度低**：期权专用项目几乎全是个人实验、测试少、无维护承诺。**策略逻辑一律不可照抄，只能抄工程模式。**
9. **数据质量**：深虚值/低流动性期权 bid/ask 常为 0 或缺值，扫描与回测必须显式过滤，否则会高估可成交性。

---

## 5. 下一步实施建议（缓存层优先）

按依赖顺序，**第一步只做缓存层，不碰下单**：

### Phase 0 — 本地缓存层（本次立即落地）

1. **建库**：SQLite（WAL）或 Parquet，目录建议 `data/opend_cache/`。核心表照抄 `options-radar-nas`：
   - `option_snapshots(contract_key, observed_at, decided_at, payload_json, UNIQUE(contract_key, observed_at))`
   - `underlying_snapshots(code, observed_at, payload_json)`
   - `chain_snapshots(underlying, expiry, observed_at, payload_json)`
   - `meta(protocol, last_rate_limit_reset, …)` 记录限频状态。
2. **写入路径**：
   - 标的 spot：`get_market_snapshot` 批量（≤400/批，内部 80 一批）→ 落库；
   - 期权链：`get_option_expiration_date` + `get_option_chain`（每到期日一次，10/30s 限频）→ 落 `chain_snapshots`（静态字段，日级 TTL）；
   - 期权报价/希腊值：`get_market_snapshot(codes)` → 落 `option_snapshots`（RTH 5–15s TTL）。
3. **限频器**：中央 `RateLimiter`（滑窗，`option_chain=10/30s`、`snapshot=60/30s`），所有 OpenD 调用先过它；写入侧记录 `decided_at/observed_at`。
4. **幂等与重放**：content-hash 去重；`--no-fetch` 只读缓存模式，便于后续回测离线重放。
5. **明确不做**：本阶段不 `unlock_trade`、不 `place_order`、不连实盘下单路径；连 OpenD 仅限只读行情。

### Phase 1 — 扫描器（基于缓存）

- 在缓存上做「低买/异动」扫描（bid/ask>0、debit 区间、delta/IV/OI/量过滤），策略逻辑全部读缓存，不直接打 OpenD。

### Phase 2 — 回测（显式防前视）

- 历史数据：外部源（Tradier sandbox / yfinance 标的 + VIX）或购买真实 OPRA 数据；
- 定价：合成 BS + 当日 IV，时点纪律照抄 `options-radar-nas`（T 信号 → T+1 开盘成交）；
- 用 in/out-of-sample + 滑点折扣，并在报告里写明「无真实历史期权链」这一前提。

---

## 附：证据与口径说明

- 限频/额度数字引自官方文档仓库 `FutunnOpen/futu-api-doc` 的 `doc_maker/source/protocol/intro.rst` 与 `q&a/Q&A.rst`。
- 期权字段/接口能力引自 `FutunnOpen/py-futu-api` 的 `futu/quote/open_quote_context.py` 与 `futu/common/pb/Qot_GetOptionChain.proto`、`Qot_GetOptionExpirationDate.proto`。
- 各仓库结论来自对其 README + 关键源码（`futu_broker.py`、`moomoo_gateway.py`、`data_fetcher.py`、`options_backtester.py`、`futu_client.py`、`analyst_backtest.py`、`db.py`、`backup_providers.py`、`config.py` 等）的逐文件阅读。
- GitHub API 匿名额度有限（本任务内已用尽），星标数来自 `search/repositories` 结果快照，存在 ±个位误差，仅作相对量级参考。
- 未发现可引用的权威「期权回测」博客；博客类证据从缺，已在正文如实标注。
