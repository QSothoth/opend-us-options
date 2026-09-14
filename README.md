# opend-us-options

Public toolkit for **US equities / same-day options** workflows on Futu / Moomoo **OpenD**.

Split out of a larger private project so offline tooling can use code + eval data without private-repo access.

## What this is
- Local **cache-first** K-line warm/read helpers (avoid burning OpenD history quota)
- Offline eval slice tooling (`evaldata/`)
- Scaffolding toward a **one-trade-per-day custody timing** bot (you pick symbol/direction; software times entry/exit)
- Scenario-labeled **real** market eval package as a GitHub Release

## What this is NOT
- Not a trading signal service
- Does **not** ship API tokens, OpenD passwords, or account secrets
- Synthetic `testdata/` is **schema smoke only** — evaluation must use Release `eval-data-v1`

## Eval data
Prefer **v2** (includes 5m + 1m):

- Release: https://github.com/QSothoth/opend-us-options/releases/tag/eval-data-v2
- Asset: `opend_us_options_eval_v2.zip`
- Primary timing: **5m** (~12 months); fine entry/exit: **1m** (~4 months)
- Also includes day + 15m for context

Coarse baseline still available: [eval-data-v1](https://github.com/QSothoth/opend-us-options/releases/tag/eval-data-v1).


## Layout
```text
cache_store.py / fetch_klines.py / rate_limit.py
backtest_scaffold.py
evaldata/
testdata/   # synthetic schema fixtures only
docs/
```

## Safety
- Prefer dry-run
- Never commit `.env`, tokens, or unrelated live dumps
- OpenD history: warm once, then read local cache only

## License
Code: MIT. Release market data is a small personal research extract for reproducible offline tests.
