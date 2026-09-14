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
- Synthetic `testdata/` is **schema smoke only** — evaluation must use a pinned real Release (`eval-data-v2` for the minute timing research below)

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
research/aggressive_payoff/  # offline source, retained 27.90 configuration and evidence
```

## Safety
- Prefer dry-run
- Never commit `.env`, tokens, or unrelated live dumps
- OpenD history: warm once, then read local cache only

## Versioned custody API

[Custody API and execution contract](custody/README.md) exposes two immutable strategy IDs and a minimal authenticated job interface. Required input: `strategy_id`, `symbol`, `direction`, and exact option `contract`; quantity defaults to1 and date to the current ET session. Paper/live mode and account are server-bound.

The new module includes a persistent one-job-per-underlying/day lock, order intents, partial-fill and cancellation handling, restart reconciliation boundaries, a paper lifecycle demo, and registry-driven real Release regression. No live broker order connector is bundled or activated. A read-only OpenD market path is bundled for **dryrun**: `python3 -m custody dryrun ...` resolves the exact contract, polls underlying + option quotes via `OpenQuoteContext`, advances the controller and logs order intents while a broker-less controller and a `dryrun`-mode service guarantee no `place_order`/`unlock_trade` call. See [custody/README.md](custody/README.md) for the `US.SKHY260918P175000` example.

## Offline aggressive timing research

[Research source, exact configuration and reproduction commands](research/aggressive_payoff/README.md).

The retained 1m research configuration has underlying payoff27.90 across the fixed83-session eval-data-v2 slice at2bps friction. This is a posthoc research result: **underlying proxy, not true option PnL**. The full report includes ablations, execution sensitivity and the historical5m comparison. This module disables networking and does not enable live trading.

The [5m follow-up](research/aggressive_payoff/five_minute_followup/REPORT.md) tests398 additional configurations: its grid winner has payoff17.22 and the highest subsequent ablation18.45. The1m27.90 configuration stays retained; the5m middle/final periods remain weaker. Exact5m rules and reproducible evidence are included.

## License
Code: MIT. Release market data is a small personal research extract for reproducible offline tests.
