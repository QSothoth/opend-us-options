# Custody API v1

调用方只需给出 **策略版本、标的、多空方向、具体期权合约**；数量默认1张、日期默认美东当天。1m与5m配置独立保存，可继续增加版本。模拟/实盘模式属于服务端配置，不能在单次请求中切换。当前已实现可运行的接口、持久化状态机和离线闭环；另有一个只读 OpenD `dryrun` 行情路径（`OpenQuoteContext`，绝不下单），真实券商下单连接仍需在部署环境接入并联调。

A small broker-neutral control API and durable order state machine for the retained1m/5m research strategies. This prepares the execution boundary for deployment while keeping every verification in this repository offline. No live broker connector is bundled or activated; a read-only OpenD market-data adapter (`custody/opend.py`) and dryrun runner (`custody/dryrun.py`) can observe quotes and advance a job without any order API.

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

`symbol` supplies underlying K lines. `contract` supplies the actual option to buy and later sell. LONG means buy CALL, SHORT means buy PUT; neither means writing a short option. A trusted instrument resolver verifies the exact code, underlying, right, same-day expiry, tradability, USD currency and contract sizing. The API does not guess an ATM contract or parse arbitrary instrument strings as authoritative metadata.

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

| strategy_id | Signal bars | Case | Historical payoff |
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
  --once
```

- `--host/--port` default to `127.0.0.1:11111` (`FUTU_HOST`/`FUTU_PORT` also work).
- `--once` runs one poll; without it the process is a resident loop (`--interval` seconds).
- `--no-frames` polls option bid/ask + underlying mark only; the default also evaluates the
  retained strategy from OpenD 1m/daily history and advances `IDLE→WATCH→ENTRY` at native
  1m/5m boundaries.
- `--no-subscribe` uses snapshots only; the default subscribes underlying + contract to QUOTE/K_1M.
- `--db` is a durable SQLite file; the account is bound to `dryrun` and cannot be reopened paper/live.

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
python3 -m custody replay --strategy orb_rvol_rsi_1m_v1 --symbol SPY --direction LONG --zip /absolute/path/opend_us_options_eval_v2.zip --out /tmp/registered_replay
python3 research/aggressive_payoff/code/verify_custody_release.py --zip /absolute/path/opend_us_options_eval_v2.zip --out /tmp/custody_release_verification.json
```

The demo uses clearly fabricated lifecycle fixtures; it is not a profitability benchmark. Replay uses only the fixed real Release, verifies SHA256 and outputs strategy/version metadata. No contract is needed for this separate underlying replay command because it cannot value or execute an option.

Fixed data: `eval-data-v2`, manifest `opend_us_options_eval_v2`, SHA256 `df93506e498be259a9654c8bf82738aa8ed3604ec2936cf4dfbed0de26aba0c6`.

The retained payoffs are repeated-history research observations: **underlying proxy, not true option PnL**. The source and contract are executable and paper-tested; live order submission, native stop support and live reconciliation must be integration-tested in the deployment environment. The `dryrun` path connects read-only to OpenD quotes but never places, cancels or unlocks a trade.
