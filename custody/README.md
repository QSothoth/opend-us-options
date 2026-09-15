# Custody API v1

## Must-trade OHLCV baseline v1

`custody_trend_1m_v1` is the first fixed custody baseline. It is dryrun-only,
accepts the caller's exact DTE 0–4 contract, consumes only today's underlying
1m bars, and has a 10:30 ET fallback. No fixed take-profit is imposed. The
version, protocol, training cases and held-out results are documented in
[`baselines/v1/README.md`](baselines/v1/README.md).

```bash
python3 -m custody baseline --slice /path/to/custody-train-dte4 \
  --out /tmp/train.json --variants baseline
python3 -m custody baseline --slice /path/to/custody-eval-2026-09-14 \
  --out /tmp/validation.json --variants baseline fixed_time \
  --freeze custody/baselines/v1/FROZEN.json
```

The replay passes every case through `CustodyService(mode='dryrun')` and
injects simulated fills at a subsequent positive-volume option bar close.
No broker is connected. Resident `custody dryrun` supports the same baseline
indicators but remains an **intent-only** live-quote observer; it does not invent
broker fills. `eval-session` below is retained as a legacy plumbing placeholder,
not the v1 performance command. The original two strategies remain immutable
research versions, not must-trade custody baselines.

调用方只需给出 **策略版本、标的、多空方向、具体期权合约**；数量默认1张、日期默认美东当天。1m与5m配置独立保存，可继续增加版本。模拟/实盘模式属于服务端配置，不能在单次请求中切换。当前已实现可运行的接口、持久化状态机和离线闭环；另有一个只读 OpenD `dryrun` 行情路径（`OpenQuoteContext`，绝不下单），真实券商下单连接仍需在部署环境接入并联调。

A small broker-neutral control API and durable order state machine for the retained1m/5m research strategies. This prepares the execution boundary for deployment while keeping every verification in this repository offline. No live broker connector is bundled or activated; a read-only OpenD market-data adapter (`custody/opend.py`) and dryrun runner (`custody/dryrun.py`) can observe quotes and advance a job without any order API.

## Custody product contract: three unmistakable layers

The custody product separates **what is traded**, **what is watched for timing**, and **what is measured**:

| Layer | Asset | Where enforced |
| --- | --- | --- |
| **1. API input** | An **exact option contract** the caller names (nearest heavy-theta expiry is typical; near-ATM / not deep OTM; need not be strict 0DTE). `JobRequest.contract`. | `custody/models.py`, `custody/job_request.schema.json`, `custody/service.py` contract resolver |
| **2. Signals / timing** | The **same-day underlying 1m** K-line only. The underlying is watched for entry/exit *timing*; it is never bought. `JobRequest.symbol`. | `custody/signals.py`, `custody/dryrun.py`, `custody/eval_session.py` |
| **3. Execution** | **That option** (LONG buys CALL, SHORT buys PUT; both close by selling the owned option). The product never writes/shorts an option. | `custody/controller.py`, `custody/service.py` |
| **4. PnL / success** | **Option path / fills only** ([`custody/pnl.py`](pnl.py)): entry/exit option premium, contract PnL and premium return. | `custody/pnl.py`, `custody/eval_session.py`, report fields |

### Underlying-proxy payoff is deprecated as custody verification

An **underlying return × multiplier** (or any "underlying-proxy payoff") is **not** custody PnL and must never be used as the custody train/eval success metric. The retained research numbers (27.90 / 17.22 / 18.45) are repeated-history **underlying-proxy** observations from `eval-data-v2`; they remain valid only for the separate offline alpha research module. `custody.pnl.underlying_proxy_pnl(...)` raises `CustodyMetricError`, and the custody eval/train loaders require a **paired** underlying+option dataset with a custody role before any PnL is computed. The dryrun **no-orders** invariant is unchanged: dryrun persists and logs intents but never submits, cancels or unlocks a trade.

## Train/research vs validation/eval/custody data (do not confuse)

| Role | Dataset | Contents | Use |
| --- | --- | --- | --- |
| **research / underlying-proxy** | `eval-data-v2` (also `eval-data-v1`) GitHub Release | underlying proxy K_DAY/K_15M/K_1M,83 sessions, **no option path** | offline indicator / alpha research and `custody replay`; **not** custody train or eval |
| **train/custody (canonical)** | `custody-train-0dte` (frozen, real paired, **true 0DTE only**) | 14 sessions 2026-08-17…2026-09-14, **36** cases, **every case `expiry == trade_date` (DTE 0)**; 10 underlyings — index SPY 12 / QQQ 5 / IWM 2, single names AAPL 5 / MSFT 4 / META 4 / NVDA 1 / TSLA 1 / AMD 1 / MU 1 (**47.2% non-ETF**); two-tier floor (index 57,000/day, single names 5,000/day); SPY share 33.3% (cap 35%); same-day **underlying 1m + near-ATM CALL/PUT option 1m** | formal custody train; paired by construction; **no DTE 1–4 padding** |
| **train/custody archive (mixed DTE, NOT formal)** | `custody-train-dte4` (frozen, real paired, **DTE≤4**) | 19 sessions 2026-08-17…2026-09-11 × 10 underlyings, **124** cases (114 option contracts, **36 true 0DTE**, DTE histogram 0:36 / 1:24 / 2:23 / 3:23 / 4:18) | archive only; demoted because non-0DTE and 0DTE trade very differently |
| **raw/opend cache (NOT train)** | `custody-train-v2window-paired` (830 cases) | the full `eval-data-v2` 1m window 2026-05-14…2026-09-11 × 10 underlyings, same-day underlying 1m + option 1m, **DTE 0…99** | parent option cache; long-DTE pairings appear because OpenD lost the pre-2026-08-21 weeklies; **do not cite as train or alpha evidence** |
| **train/custody-starter** | `custody-train-2026-09-08_11` (tiny real paired starter) | 4 recent sessions × 3 liquid underlyings (SPY/QQQ/AAPL), same-day **underlying 1m + near-ATM option 1m**, 12 cases | plumbing/measurement starter only; **not** the main train |
| **validation/eval/custody** | `custody-eval-2026-09-14` (frozen slice, zipped under the job `out/`) | same-day **underlying 1m + option 1m** OHLCV for three real2026-09-14 jobs | must-trade data plumbing and custody runtime validation |

`eval-data-v2` is **underlying-only** and therefore **cannot be custody train**: it has no option series, so it can never express option PnL. Do not relabel v2 as custody train. Both custody train and validation must contain **paired** same-day underlying 1m + option 1m (or quote path) for every case, and the offline loaders enforce this (`custody.offline.assert_paired_slice`, `custody.marketdata.require_paired_bars`).

The custody product must complete **exactly one entry+exit per day** (flatten before close). The retained research gates (`orb_rvol_rsi_1m_v1`, `retest_rvol_adx_5m_v1`) can `no_entry` all day and are therefore **not** the custody eval success criterion. The custody eval slice is real OpenD market data only; no synthetic prices. Keep the research Release available for replay — it is not the custody eval set.

### Validation/eval slice (`eval/custody`) — paired underlying+option

Locked cases for `trade_date = 2026-09-14`:

| Underlying | Direction | Contract |
| --- | --- | --- |
| US.QQQ | LONG | US.QQQ260914C705000 |
| US.SKHY | SHORT | US.SKHY260918P175000 |
| US.BABA | LONG | US.BABA260918C109000 |

Expiry/DTE: `US.QQQ260914C705000` is **0DTE** (expiry == trade date); `US.SKHY260918P175000` and `US.BABA260918C109000` are **4 DTE**. All three are near-ATM (within ~0.7% of strike at open/close/mid). The custody / 末日 product targets 0DTE, so the two 4-DTE contracts are retained only for data-plumbing eval and are **flagged for the supervisor**: replace them with same-day contracts before treating the validation slice as strictly 0DTE. They are deliberately not silently rewritten.

Each case carries the same-day underlying 1m and option 1m series plus `manifest.json` (bar counts, time range, fetch time, OpenD host, per-file SHA256) and `cases.json`. Fetch once (read-only, quota-aware) and reuse the files:

```bash
python3 -m custody fetch-eval --out /path/to/custody-eval-2026-09-14
python3 -m custody eval-session --slice /path/to/custody-eval-2026-09-14 --out /tmp/eval-session.json
```

`eval-session` now **refuses any non-paired or non-custody dataset** (it calls `assert_paired_slice` + `assert_custody_role`) and emits **option-only PnL fields**:

- per case `option_pnl`: `entry_price`, `exit_price`, `qty`, `multiplier`, `option_pnl`, `option_return`, `basis`;
- report `pnl_summary`: `total_option_pnl`, `case_count`, `one_round_trip_count`;
- report flags `success_metric: "option_pnl"`, `metric_asset: "option_contract"`, `underlying_proxy_metric_forbidden: true`.

It loads the three cases, feeds the shared provider with the frozen underlying+option 1m bars and needs **no multi-day warmup**. Its timing policy is an explicitly-labelled placeholder (`DeadlineFallbackPolicy`), not an alpha; it records the real option 1m bar closes as the reference fills.

### Canonical train (`train/custody`, TRUE 0DTE frozen)

The canonical custody train set is the frozen **`custody-train-0dte`** slice at
`/workspace/pi-jobs/custody-train-0dte/out/custody-train-0dte` (zip + `.sha256` beside it):

- **true 0DTE only** — every case has `expiry == trade_date` (DTE 0). The builder refuses to pad with DTE 1–4: non-0DTE and 0DTE trade very differently (non-0DTE fills are often poor; 0DTE volume is high and fillable);
- **coverage** — 14 sessions **2026-08-17 → 2026-09-14**, **36** paired cases, **10 underlyings**: index ETFs **US.SPY 12**, **US.QQQ 5**, **US.IWM 2** (19 total) and single names **US.AAPL 5**, **US.MSFT 4**, **US.META 4**, **US.NVDA 1**, **US.TSLA 1**, **US.AMD 1**, **US.MU 1** (17 total; **47.2% non-ETF**);
- **contract per (symbol, session)** — the near-ATM listed strike nearest the session first-bar underlying open, with **both** the CALL and PUT ATM series fetched; the traded right is the side with the **higher same-day option volume** (LONG buys CALL, SHORT buys PUT). No scenario label is used;
- **two-tier volume floor** — **index 57,000 contracts/day**, auto-derived as 10% of the pooled **SPY/QQQ/IWM** ATM 0DTE median day-volume (median 570,485); **single names 5,000/day**, 10% of the index floor (min 1,000). The lower single-name tier deliberately preserves Mag7/hot names that are below SPY-sized volume: the previous one-tier 57,000 floor dropped META ×4, MSFT ×4, MU ×1, AMD ×1 and AAPL 2026-09-11 ×1; all 17 single-name candidates now clear the 5,000 floor (`filtered_below_floor` = 0);
- **diversification (SPY share cap 35%)** — SPY is the deepest 0DTE underlying, so the builder caps its share at **35%** by holding out the **lowest-day-volume SPY sessions** (the 6 held-out sessions are recorded in `spy_cap_dropped`, not silently deleted). Actual mix: **SPY 33.3%, index ETFs 52.8%, single names 47.2%**;
- **AMZN / GOOGL** — requested as Mag7 七姐妹 but **not recoverable**: OpenD's option quota is fully spent (`option: 60/60`) and neither has a warm same-day expiry group, so they never enter `zero_dte_targets`. They are listed in the manifest `unavailable_symbols`; no DTE≥1 contract is substituted;
- **timing asset** — same-day **underlying 1m**, reused from the `eval-data-v2` Release through 2026-09-11 and read-only OpenD for 2026-09-14;
- **PnL asset** — the same-day **option 1m**, read-only OpenD;
- **max_dte** — **0**; **zip SHA256** — `65398673c6617ef0a013c1795936016404babe4a1d4fc1777e16502c2621c7d3`;
- **OpenD retention** — option history is quota-accounted per `(underlying, expiry)` group and brand-new groups were refused during the build. Only warm same-day groups were recoverable: SPY 18 of 20 window sessions, QQQ the Friday/2026-09-14 chains, IWM 2026-08-21 and 2026-09-11, and single names only on their warm same-day expiries (AAPL 5 sessions; MSFT/META 4; NVDA/TSLA/AMD/MU 1). The SPY share cap then held out 6 SPY sessions. These are recorded as `opend_gaps` / `unavailable_symbols`; **no DTE≥1 contract is substituted** for a missing 0DTE group.

```bash
python3 -m custody eval-session --slice /workspace/pi-jobs/custody-train-0dte/out/custody-train-0dte --out /tmp/train-report.json
```

Build it (read-only OpenD, quota-aware):

```bash
python3 -m custody fetch-train-0dte --out out/custody-train-0dte --v2-dir /path/to/opend_us_options_eval_v2
```

> **Prospective freeze (grow the formal train while chains exist):** freeze each new session's
> **same-day (0DTE)** paired underlying+option 1m bars the same day and append them to the
> canonical set; OpenD drops old weeklies and the option quota blocks new expiry groups, so the
> 0DTE set only grows with a daily freeze. See `CANONICAL_TRAIN_DIR` / `PROSPECTIVE_FREEZE_NOTE`
> in [`train_window.py`](train_window.py) and [`train_0dte.py`](train_0dte.py).

### Mixed-DTE archive (`custody-train-dte4`, NOT formal train)

The former canonical train **`custody-train-dte4`** (19 sessions 2026-08-17 → 2026-09-11,
**124** cases, DTE histogram 0:36 / 1:24 / 2:23 / 3:23 / 4:18, zip SHA256
`d72e190e6467f780782f809302a51f9dc8d6f72a00885c2dfb1aa96bf65c51f0`) is demoted to
**archive**. 88 of its 124 cases are DTE 1–4, which trade differently from 0DTE. It is kept only
because earlier reports reference it and must not be cited as the canonical train.

### Raw/parent OpenD cache (NOT formal train)

The expanded builder output `custody-train-v2window-paired` (830 cases, window 2026-05-14 →
2026-09-11, DTE 0…99, zip SHA256 `b3536cb6a1e9eb00103cbb43442b3101679e879f3fe2b6db23dd4e490810a8c6`)
is retained only as the **parent option cache**. It is demoted from canonical
train because long-DTE pairings are a data-availability artifact, not a heavy-theta product, and
the formal train is now strictly true-0DTE (`custody-train-0dte`).

The `fetch-train` builder still emits this raw cache:

```bash
python3 -m custody fetch-train --out out/custody-train-v2window-paired --v2-dir /path/to/opend_us_options_eval_v2
```

### Train starter (`train/custody-starter`, real paired)

`eval-data-v2` is underlying-only and is **refused** by the custody metric path. The tiny starter is retained for plumbing/measurement only:

```bash
python3 -m custody fetch-train-starter --out out/custody-train-2026-09-08_11
python3 -m custody eval-session --slice out/custody-train-2026-09-08_11 --out /tmp/train-report.json
```

`fetch-train-starter` picks, per session × liquid underlying, the listed strike nearest the session first-bar underlying open (CALL for LONG, PUT for SHORT) at a fixed short-dated expiry, then freezes the **same layout** as the validation slice (`underlying/` + `option/` + `cases.json` + `manifest.json`, role `train/custody-starter`). It is quota-aware (option chain cached per underlying/expiry/right; each history series fetched once) and read-only. The starter is intentionally small: a plumbing/measurement benchmark, not an alpha. Series that span several sessions are concatenated per code and filtered by session at read time.

Starter status (the original standalone run): 12 cases (4 sessions 2026-09-08…11 × US.SPY/US.QQQ/US.AAPL), 13 frozen series, every case 390 underlying bars + 390–405 option bars. See the job `out/REPORT.md`.

## One market-data boundary: frozen slice ↔ live OpenD

`custody/marketdata.py` defines the shared `Bar` type and the `MarketDataProvider` protocol (`quote`, `underlying_mark`, `history_bars`, `current_bars`). Two providers implement it:

- `custody.opend.OpenDMarket` — read-only live OpenD (`OpenQuoteContext` only), used by `dryrun`.
- `custody.offline.OfflineMarket` — reads the frozen custody eval slice files.

Both return the same `Bar` records, so `custody.eval_session`, `OpenDHistorySource` and the custody controller are provider-agnostic: **offline = swap in `OfflineMarket`, live = swap in `OpenDMarket`**, no controller rewrite. `OpenDMarket.history_bars` wraps `request_history_kline`; `OpenDMarket.current_bars` wraps `get_cur_kline`.

Honesty note: 1m history is trade **OHLCV** for both the underlying and the option, offline and live. Option **bid/ask** exists only live; the frozen slice does not contain NBBO, so `OfflineMarket.quote` returns `None` by default and never fabricates a spread. The placeholder must-trade stub may opt into the clearly-labelled `option_bar_close` mapping (bid == ask == frozen bar close) for plumbing only; live always uses real option quotes. The switch stays honest because both providers expose the same methods and the fill basis is recorded as `option_bar_close` offline versus a live quote.


## One request

`POST /v1/jobs` with `Authorization: Bearer <server token>` and JSON:

```json
{
  "strategy_id": "orb_rvol_rsi_1m_v1",
  "symbol": "SPY",
  "direction": "LONG",
  "contract": "<exact broker option code>",
  "max_qty": 1
}
```

Only the first four fields are required. `max_qty` defaults to1; `trade_date` defaults to the current **America/New_York** date. Callers cannot override indicator parameters, account, or paper/live mode. Unknown fields (including the old per-request `dry_run`) are rejected. The server is bound to an account and defaults to `mode='paper'`; a database account cannot be reopened in another mode.

`symbol` supplies underlying K lines **for entry/exit timing only**; `contract` supplies the actual option to buy and later sell and is the asset whose fills are measured. LONG means buy CALL, SHORT means buy PUT; neither means writing a short option. A trusted instrument resolver verifies the exact code, underlying, right, same-day expiry, tradability, USD currency and contract sizing. The API does not guess an ATM contract or parse arbitrary instrument strings as authoritative metadata. Custody PnL is computed from the option fills ([`custody/pnl.py`](pnl.py)); an underlying-proxy payoff is never a custody success metric.

Example response fields:

```json
{
  "job_id": "<stable account-symbol-date ID>",
  "state": "IDLE",
  "mode": "paper",
  "strategy_id": "orb_rvol_rsi_1m_v1",
  "strategy_hash": "<SHA256 of immutable configuration>",
  "case_id": "536f0789fb0d",
  "position_qty": 0,
  "attention": null
}
```

Other routes:

- `GET /v1/strategies`: available version IDs and provenance.
- `GET /v1/jobs/{job_id}`: status, owned quantity and attention state.
- `POST /v1/jobs/{job_id}/stop`: stop watching or request cancellation/liquidation; does not pretend that a position is already closed.

All routes require authentication. `create_app(service, token)` returns a framework-neutral WSGI app; no listener starts on import. Bind a server in the deployment environment and keep market/broker event ingestion internal. The account is server-bound, not supplied by the HTTP caller.

## Retained strategies and N baselines

| strategy_id | Signal bars | Case | Research underlying-proxy payoff (not custody PnL) |
| --- | --- | --- | ---: |
| orb_rvol_rsi_1m_v1 | 1m | 536f0789fb0d | 27.90 |
| retest_rvol_adx_5m_v1 | 5m | 2c0ecc056bd3 | 18.45 |

The strategy files are byte-for-byte copies of the retained research configurations. `strategies/index.json` binds each version to its file SHA256 and canonical case ID. Add a new immutable ID/file to retain another candidate. Never replace v1 with different parameters. The persistent service also pins observed ID/hash pairs across restarts. Existing jobs snapshot their exact config. No `best`/`latest` alias changes a caller's selected baseline.

The order runtime currently supports the exit modules used by these two configurations: initial close loss, fast failure, ordinary trailing, maximum hold, scheduled flatten and an underlying safety monitor. Configs requiring other exit modules are rejected at job creation until their runtime is implemented. Single-symbol feature input also rejects relative-strength rules needing benchmark symbols. This makes adding a registry entry explicit rather than silently dropping unsupported behavior.

## Runtime composition

```python
from custody.registry import Registry
from custody.service import CustodyService
from custody.http import create_app
from custody.controller import Controller
from custody.signals import SignalProvider

service = CustodyService(
    db_path="/persistent/custody.sqlite",
    account="<server account>",
    contracts=instrument_resolver,
    calendar=exchange_calendar,
    mode="paper"
)
app = create_app(service, token=server_token)
worker = Controller(service, broker=paper_adapter)
signals = SignalProvider(service.registry)
```

The named resolver/calendar/adapter variables above are deployment-injected objects implementing `ports.py`; they are not hidden network integrations. `create_job` receives an explicit server time in the Python API; HTTP uses the real current clock by default. Active jobs cannot be backdated. An actual exchange calendar must provide holidays and early closes; the runtime never substitutes a weekday-only calendar. Flatten is the earlier of the strategy clock and15minutes before the actual close.

For each active job, the worker runs independently of candle arrival:

1. Resolve/reconcile pending broker orders by their stable client IDs.
2. Obtain a fresh option bid/ask and, where available, a fresh underlying safety mark from the deployment's market adapter.
3. At a native bar boundary, call `SignalProvider.evaluate(...)` with completed1m history, prior completed daily OHLC, and the trusted session calendar. It returns a `Frame`; 5m indicators see only completed5m bars. The provider invokes the original research indicators in an isolated subprocess and validates history completeness/time ordering. It neither downloads quotes nor fabricates future prices. Use a consistent history anchor across restarts because Wilder indicators carry historical state.
4. Call `Controller.step(job_id, now, quote=..., frame=..., underlying_mark=..., mark_as_of=...)`. Omit frame between completed bars. This runs the time/safety checks even during a candle outage, then records/submits a bounded limit-order intent.

`SignalProvider` input records are documented in `signals.py`: minute OHLCV contains timezone-aware ISO `close_time`; daily OHLC contains prior `date`; `session_closes` maps session date to aware close timestamp. Its actual single-symbol prefixes are checked against the fixed Release research features in `verification_release.json`. Missing/future bars or current-day daily OHLC are rejected. No bare research imports or global socket modifications leak into the host API process.

A deployment broker must map `BUY_OPEN` and `SELL_CLOSE` explicitly, enforce buying power/limits and close-only owned quantity, and durably map client IDs to broker order IDs. Order updates carry monotonic sequences and cumulative fills. The first positive BUY update must include actual `first_fill_at`, its contemporaneous underlying mark, and average option fill price. Option fill prices are actual broker/paper-adapter observations, not estimates inferred from underlying returns.

## What the state machine guarantees locally

- SQLite transactions enforce one job per **account + underlying + ET date**, across both strategy IDs and both directions. Identical requests return the same job. A different request conflicts, even after a completed or missed trade. Unresolved prior-session jobs also block new jobs for the underlying.
- The state path is IDLE→WATCH→ENTRY→IN→EXIT→DONE. Partial entry can move directly from ENTRY to EXIT. Only confirmed fills change owned quantity. Partial fills are still one trade, not additional entries.
- Entry intent is unique. It is persisted before broker I/O. A crash in DISPATCHING or a transport timeout leaves an uncertain order for reconciliation; the runtime never blindly resubmits it. This is not a claim of broker-side exactly-once execution.
- Exiting cancels the remaining entry first and waits for an authoritative terminal entry update. A cancel-request acknowledgment alone is insufficient. Liquidation orders are close-only and sized to confirmed remaining quantity. A partially filled/canceled exit can be replaced for the residual only.
- Stale/crossed/wide quotes cannot generate a new order. Unsent stale orders are discarded; sells can be replaced using a fresh quote. Entry timeout cancels the remainder and never starts a second entry. Execution policy is server-side and must be calibrated separately from the historical signal parameters.
- Scheduled flatten does not depend on receiving the next K line. Missing quotes or unknown broker status leave the position in EXIT with an attention state, never a false DONE. Only zero owned quantity and a terminal entry permit completion.
- Order events, config snapshots and pending intents survive restart. Broker lookup is required before acting on uncertain orders. External/manual positions and broker-side limit enforcement remain the adapter's responsibility.

Default software execution-policy values are5seconds quote age,15seconds frame age,30seconds entry timeout and30% maximum relative spread. These are explicit engineering defaults, not thresholds calibrated on historical option quotes. They are independently configurable on the server without overwriting strategy files.

The software underlying safety monitor needs fresh marks and a functioning worker. It is **not** a broker-native resting conditional stop. Backtests used an idealized1m intrabar resting safety-stop simulation; live limit fills, asynchronous timing, partial fills, spread and the software watchdog can differ. No stop or scheduled flatten can guarantee a fill during outages or illiquidity.

## OpenD dryrun live path (read-only, no orders)

`custody/opend.py` + `custody/dryrun.py` add a resident, real OpenD market path that can be
observed without ever submitting an order. It uses `OpenQuoteContext` only (never
`OpenSecTradeContext`, `place_order` or `unlock_trade`) and runs the custody service in a
server-bound `mode='dryrun'` account.

Hard gate: dryrun builds a `Controller` with **no broker**, so `submit`/`cancel` are
unreachable, and `CustodyService.dispatch_next` itself refuses to dispatch in dryrun mode.
Order intents are persisted and logged but never leave the process (`submitted=false`).

```bash
python3 -m custody dryrun \
  --strategy orb_rvol_rsi_1m_v1 \
  --symbol US.SKHY --direction SHORT \
  --contract US.SKHY260918P175000 \
  --db /tmp/custody-dryrun.sqlite \
  --wxpusher-spt "$CUSTODY_WXPUSHER_SPT" \
  --once
```

- `--host/--port` default to `127.0.0.1:11111` (`FUTU_HOST`/`FUTU_PORT` also work).
- `--once` runs one poll; without it the process is a resident loop (`--interval` seconds).
- `--no-frames` polls option bid/ask + underlying mark only; the default also evaluates the
  retained strategy from OpenD 1m/daily history and advances `IDLE→WATCH→ENTRY` at native
  1m/5m boundaries.
- `--no-subscribe` uses snapshots only; the default subscribes underlying + contract to QUOTE/K_1M.
- `--db` is a durable SQLite file; the account is bound to `dryrun` and cannot be reopened paper/live.
- `--wxpusher-spt` (or `CUSTODY_WXPUSHER_SPT`) pushes each new `order_intent` to WxPusher; omit it to stay silent.

Unlike paper/live, dryrun accepts an exact contract whose expiry is **on or after** the
current ET session (so the 4-DTE `US.SKHY260918P175000` put can be watched on `2026-09-14`).
Same-day-only enforcement is unchanged for paper/live. `OpenDTradingCalendar` reads
`request_trading_days` for holidays and early closes; no order can be submitted regardless.

The dryrun units are mock-tested (`custody/tests/test_dryrun.py`) and statically checked to
reference no trade API. No test connects to OpenD or burns history quota.

## Offline commands

```bash
python3 -m custody strategies
python3 -m custody.demo
python3 -m unittest discover -s custody/tests -v
python3 -m custody eval-session --slice /path/to/custody-eval-2026-09-14 --out /tmp/validation-report.json
python3 -m custody eval-session --slice /workspace/pi-jobs/custody-train-0dte/out/custody-train-0dte --out /tmp/train-report.json
python3 -m custody fetch-train-0dte --out out/custody-train-0dte --v2-dir /path/to/opend_us_options_eval_v2  # formal true-0DTE train (read-only OpenD)
python3 -m custody fetch-train --out out/custody-train-v2window-paired --v2-dir /path/to/opend_us_options_eval_v2  # raw/parent cache
python3 -m custody fetch-train-starter --out out/custody-train-2026-09-08_11
python3 -m custody replay --strategy orb_rvol_rsi_1m_v1 --symbol SPY --direction LONG --zip /absolute/path/opend_us_options_eval_v2.zip --out /tmp/registered_replay
python3 research/aggressive_payoff/code/verify_custody_release.py --zip /absolute/path/opend_us_options_eval_v2.zip --out /tmp/custody_release_verification.json
```

The demo uses clearly fabricated lifecycle fixtures; it is not a profitability benchmark. Replay uses only the fixed real Release, verifies SHA256 and outputs strategy/version metadata. That replay command is a separate **underlying-proxy research** path and cannot value or execute an option, so it must never be cited as custody PnL.

Fixed research data: `eval-data-v2`, manifest `opend_us_options_eval_v2`, SHA256 `df93506e498be259a9654c8bf82738aa8ed3604ec2936cf4dfbed0de26aba0c6`.

The retained research payoffs are repeated-history observations: **underlying proxy, never custody option PnL**. The source and contract are executable and paper-tested; live order submission, native stop support and live reconciliation must be integration-tested in the deployment environment. The `dryrun` path connects read-only to OpenD quotes but never places, cancels or unlocks a trade.

### Optional phone alerts (WxPusher)

Pass `--wxpusher-spt "$CUSTODY_WXPUSHER_SPT"` (or export `CUSTODY_WXPUSHER_SPT`) to push
every new dryrun `order_intent` to [WxPusher](https://wxpusher.zjiecode.com). The runner uses the
proven SPT GET shape:

```text
GET https://wxpusher.zjiecode.com/api/send/message/{SPT}/{urlencoded message}
```

The SPT is a **secret**. Never hard-code or commit it: keep it in the environment or a
local untracked file and pass it at runtime. Push failures are swallowed, so a flaky phone
notification can never stop the resident dryrun loop. The alert is informational only; the
intent is still persisted locally and the dryrun path never submits, cancels or unlocks a trade.
