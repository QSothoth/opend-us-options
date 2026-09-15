# Real eval slice (OpenD underlying K-lines)

This package builds and validates a **fixed real-market evaluation set** used as
the development / regression benchmark for US options timing work.

> **Role note (custody):** this slice is **research / underlying-proxy only**:
> underlying K-lines with **no option path**. It is **not** custody train and
> **not** custody validation. The custody product requires paired same-day
> underlying 1m + option 1m, and the custody metric path refuses this slice
> (`assert_paired_slice` / `assert_custody_role`; see [`custody/pnl.py`](../custody/pnl.py)).
> The custody validation/eval set is the separate `custody-eval-2026-09-14`
> slice and the canonical paired train is `custody-train-v2window-paired`
> (this slice's underlying 1m paired with OpenD option 1m), with the tiny
> `custody-train-2026-09-08_11` retained as a `train/custody-starter`. Both are
> documented in [`custody/README.md`](../custody/README.md).

## Important

| Dataset | Role |
| --- | --- |
| **GitHub Release `eval-data-v1`/`eval-data-v2`** (`opend_us_options_eval_*`) | **Research / underlying-proxy benchmark** — real OpenD QFQ **underlying** K-lines only, **no options** |
| `scripts/opend_us_options/testdata/` | **Schema / CI smoke only** — synthetic, **not** a performance baseline |

Do **not** treat synthetic `testdata/` as a measure of strategy quality.

## Contents of the Release artifact

```
opend_us_options_eval_v1/
  manifest.json
  klines_day.parquet
  klines_15m.parquet
  scenario_labels.parquet
  eval.duckdb
  CHECKSUMS.sha256
  README.md
```

Large parquet/duckdb files are **not** committed to git; download from the
Release tag `eval-data-v1`.

## How to warm cache (once) then export

```bash
# venv with futu-api / pandas / pyarrow / duckdb
source /workspace/venvs/opend-bt/bin/activate
cd scripts/opend_us_options

SYMBOLS="US.SPY US.QQQ US.IWM US.AAPL US.NVDA US.TSLA US.MU US.AMD US.META US.MSFT"

# 1) dry-run plan (no OpenD)
python fetch_klines.py --symbols $SYMBOLS --ktype K_DAY --start 2022-01-01 --end 2026-09-12
python fetch_klines.py --symbols $SYMBOLS --ktype K_15M --start 2025-09-11 --end 2026-09-12

# 2) execute once (prints quota before/after)
python fetch_klines.py --symbols $SYMBOLS --ktype K_DAY --start 2022-01-01 --end 2026-09-12 --execute
python fetch_klines.py --symbols $SYMBOLS --ktype K_15M --start 2025-09-11 --end 2026-09-12 --execute

# 3) label + export + validate (cache-only)
OUT=/path/to/opend_us_options_eval_v1
python evaldata/label_scenarios.py --out $OUT/scenario_labels.parquet --dist-out $OUT/scenario_dist.csv
python evaldata/export_real_slice.py --out-dir $OUT --labels $OUT/scenario_labels.parquet
python evaldata/validate_real_slice.py --dir $OUT
```

## Query with DuckDB

```bash
duckdb eval.duckdb -c "SELECT symbol, COUNT(*) FROM klines_day GROUP BY 1"
duckdb eval.duckdb -c "SELECT * FROM v_scenario_counts"
```

Or read parquet directly with pandas/polars.

## Scope notes

- Underlying **K_DAY** + **K_15M** only (no 1m/tick).
- Option contract history is **not** in this slice (OpenD limitation). Paired
  same-day underlying+option 1m now exists in the separate custody
  `custody-eval-2026-09-14` (validation) and `custody-train-v2window-paired`
  (canonical train, which reuses this slice's underlying 1m and adds OpenD
  option 1m) slices; this research Release deliberately stays underlying-only and
  must not be used as custody train or custody PnL evidence.
- Private / self-use Release — not for public redistribution.
