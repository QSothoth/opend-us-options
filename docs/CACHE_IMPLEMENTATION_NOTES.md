# Design notes — OpenD local K-line cache + cautious backtest scaffold

## What this phase is (and is not)

- **Is**: a local cache for *underlying* K-lines + a minimal, cache-only backtest
  scaffold. Read-only OpenD (`OpenQuoteContext` only).
- **Is not**: option-chain/Greeks caching (OpenD has no historical option data),
  live scanning, trade execution, or production cron/`quant_flow` wiring.

## Why underlying K-lines only

OpenD's `get_option_chain` / `get_market_snapshot` return *current* state. There
is no `as-of` history for chains or Greeks, so a real options backtest needs an
external data source (Tradier/yfinance/ThetaData/Databento/CBOE/…). Until then,
underlying daily bars let us exercise the engineering patterns (cache, quota,
no-look-ahead) on a cheap, honest dataset.

## Quota protection design

Official limit: `request_history_kline` ≤ 60 calls / 30s.

1. `cache_store` keeps a per-symbol/ktype **fetched-ranges manifest**
   (`*.fetched.json`). Every span successfully requested from OpenD is recorded
   there, *including* spans that returned zero bars (holidays).
2. `missing_ranges(symbol, ktype, start, end)` subtracts the recorded spans from
   the requested window. Only the remaining gaps are eligible for fetching.
3. `fetch_klines.py` computes the plan **before** any OpenD call and fetches only
   missing spans.
4. `rate_limit.SlidingWindowRateLimiter` defaults to 50 calls / 30s to stay under
   the 60/30s cap.
5. `get_bars()` never calls OpenD — it can only read local files.

Net effect: after a symbol/interval/range is cached, **all** subsequent reads
come from local cache; OpenD is never asked for the same K-line twice.

## No look-ahead (未来函数) prevention

Rules implemented and tested by `backtest_scaffold.py`:

- Decision inputs are restricted to bars with `time <= T`.
- `NoFutureBars.upto()` / `bar_at()` raise `LookAheadError` on any future read.
- The **only** sanctioned future read is `next_bar_after(T)`, used for the T+1
  execution fill and never fed back into the signal.
- Timing: signal on close of day `T` → fill at open of `T+1` → exit on opposite
  cross close of `T` → fill at open of `T+1`; end-of-data liquidation marks to
  the final bar's close (no T+1 bar exists).
- `selfcheck_no_lookahead()` runs before every backtest and aborts on failure.

Why the guard is explicit instead of relying on convention: pandas makes it easy
to accidentally shift/vectorize a future value into a decision. A hard guard that
raises on the bad access converts that class of bug into a loud failure.

## Storage decisions

- **Parquet preferred, CSV fallback**: parquet gives fast columnar reads and
  stable dtypes; CSV keeps the module usable when `pyarrow` isn't installed.
- **Partitioned by symbol + ktype** (`klines/<SYMBOL>/<KTYPE>.*`) so the cache
  can grow to many symbols/interval types without one giant file.
- **`time` = epoch seconds** (from OpenD's naive market-local string). Used only
  for ordering/range filtering; not timezone-correct scheduling.
- **Atomic writes**: write to a `.tmp` file then `os.replace`, so a crash never
  leaves a half-written cache file.

## Read-only guarantee

`fetch_klines.py` imports and constructs `OpenQuoteContext` only. `unlock_trade`,
`place_order`, and `OpenSecTradeContext` are never imported. There is no code path
that can submit an order.

## Known gaps / next steps

- No historical option chain/Greeks (fundamental OpenD limitation).
- Cache assumes a single writer process (no cross-process locking).
- Toy SMA-cross strategy is a scaffold; swap in real logic later.
- Consider migrating to SQLite (WAL) snapshot tables if we later add
  per-contract `observed_at`/`decided_at` snapshots (see the options-radar-nas
  schema in the research report).
