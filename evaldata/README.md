# Real eval slice (OpenD underlying K-lines)

This package builds and validates a **fixed real-market evaluation set** used as
the development / regression benchmark for US options timing work.

> **Role note (custody):** this underlying-proxy slice is the **train/research**
> dataset. The custody product eval set is the separate same-day underlying+option
> 1m `custody-eval-2026-09-14` slice documented in
> [`custody/README.md`](../custody/README.md). Do not use this slice as the
> custody must-trade eval.

## Important

| Dataset | Role |
| --- | --- |
| **GitHub Release `eval-data-v1`** (`opend_us_options_eval_v1`) | **Evaluation benchmark** — real OpenD QFQ K-lines |
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
- Option contract history is **not** in this slice (OpenD limitation); contract
  path may be added later via daily snapshots.
- Private / self-use Release — not for public redistribution.
