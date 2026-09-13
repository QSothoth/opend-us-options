# OpenD local K-line cache + cautious backtest scaffold

Status: **read-only / toy backtest scaffold**. No `unlock_trade`, no
`place_order`, no production cron or `quant_flow` wiring.

## Why this exists

OpenD has **no historical option chain or historical Greeks** — `get_option_chain`
and `get_market_snapshot` are "current state" APIs only. So this phase caches
**underlying K-lines** (not options) so we can run *toy* backtests without
repeatedly hitting `request_history_kline`.

Everything here follows three rules copied from the ecosystem research:

1. **Cache-first** — read local cache; only fetch what is missing.
2. **Quota protection** — never re-request a span already fetched from OpenD.
3. **No look-ahead (未来函数)** — signal on close of day `T`, fill at open of `T+1`;
   decisions never see a bar with `time > T`.

## Files

| File | Purpose |
|---|---|
| `cache_store.py` | Local cache (parquet preferred, CSV fallback), partitioned by `symbol + ktype`. Never calls OpenD. |
| `fetch_klines.py` | Warm-cache CLI. Dry-run by default; `--execute` fetches **only missing spans**. |
| `backtest_scaffold.py` | Toy SMA-cross backtest on cached daily bars, with a no-look-ahead guard + self-check. |
| `rate_limit.py` | Sliding-window limiter (50 calls / 30s by default, under the 60/30s official cap). |

## Dependencies

- `pandas` (required)
- `pyarrow` (optional — enables parquet; otherwise the cache falls back to CSV)
- `futu-api` (required only for `fetch_klines.py --execute`)

## Cache layout

```
cache/
  README.md
  .gitkeep
  klines/
    US.SPY/
      K_DAY.parquet          # or K_DAY.csv when pyarrow is absent
      K_DAY.fetched.json     # quota-protection manifest: spans already requested
    US.QQQ/
      ...
```

- `K_DAY.parquet` / `K_DAY.csv` — normalized bars: `time, open, high, low, close, volume, turnover`
  (`time` = epoch seconds, ascending, unique).
- `K_DAY.fetched.json` — records every span successfully requested from OpenD.
  Even a span that returned **zero bars** (e.g. a long holiday) is recorded, so we
  never waste quota re-requesting it.

Data files are gitignored; only `README.md` and `.gitkeep` are committed (see
`.gitignore.snippet` at the repo root).

## Usage

### 1. Warm the cache

```bash
# Dry-run (default): print the plan, make NO OpenD calls
python scripts/opend_us_options/fetch_klines.py \
    --start 2023-01-01 --end 2024-12-31

# Actually fetch missing spans (read-only OpenQuoteContext only)
python scripts/opend_us_options/fetch_klines.py \
    --start 2023-01-01 --end 2024-12-31 --execute

# Override the allowlist / ktype
python scripts/opend_us_options/fetch_klines.py \
    --symbols US.SPY US.QQQ --ktype K_DAY \
    --start 2023-01-01 --end 2024-12-31 --execute
```

Default allowlist: `US.SPY, US.QQQ, US.AAPL, US.NVDA, US.TSLA, US.MU, US.SNDK, US.SKHY`.
Override with `--symbols` or the `OPEND_US_OPTIONS_ALLOWLIST` env var (comma-separated).

When `--execute` runs it prints `get_history_kl_quota` before/after (best-effort)
and sleeps/rate-limits via `rate_limit.history_kline_limiter()`.

### 2. Run the toy backtest

```bash
python scripts/opend_us_options/backtest_scaffold.py \
    --symbol US.SPY --start 2023-01-01 --end 2024-12-31 \
    --short 20 --long 50
```

The backtest **refuses to run** (exit code 2) if the required bars — including
SMA warmup before `--start` — are not fully cached. It tells you the exact
`fetch_klines.py` command to run.

## No look-ahead guarantee (implementation)

`backtest_scaffold.NoFutureBars` wraps the bars and enforces:

- `upto()` / `bar_at()` → **only** bars with `time <= T` may be read for decisions;
  anything else raises `LookAheadError`.
- `next_bar_after(T)` → the *single* sanctioned future read, used **only** for the
  T+1 execution fill. It is never fed back into the signal.
- A `selfcheck_no_lookahead()` runs before every backtest and aborts on failure.

Signal/execution timing:

- Signal computed on close of day `T` using bars `<= T`.
- Fill at **open of `T+1`**.
- Still-open position at end of data → liquidated at final bar close (no T+1 bar exists).

## Quota protection guarantee

- `fetch_klines.py` computes missing ranges **before** any OpenD call.
- Every successfully requested span is recorded in `*.fetched.json`.
- `cache_store.missing_ranges()` subtracts recorded spans, so a range is fetched
  **at most once**. After that, all reads (`get_bars`) are served from local cache.
- Rate limit default is 50 calls / 30s (official cap: 60 / 30s).

## Read-only guarantee

Only `OpenQuoteContext` is used (in `fetch_klines.py`). `unlock_trade`,
`place_order`, and `OpenSecTradeContext` are never imported or called.

## Known limitations

- Underlying K-lines only; no historical option chain / Greeks (OpenD does not
  provide them).
- `time` is epoch seconds derived from OpenD's naive market-local time; used for
  ordering/filtering, not timezone-correct scheduling.
- Toy SMA-cross strategy is a scaffold, not an alpha signal.
- Parquet/CSV cache is append-merge on write; concurrent writers are not
  coordinated (single-process use assumed).
