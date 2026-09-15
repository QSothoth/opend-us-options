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

## Custody product contract (input=option, signals=underlying, PnL=option)

The custody bot separates three layers:

- **input** — an **exact option contract** (nearest heavy-theta expiry, near-ATM / not deep OTM; need not be strict 0DTE);
- **signals** — the **same-day underlying 1m K-line**, watched for entry/exit *timing* only;
- **PnL** — the **option path / fills only**. An underlying return × multiplier ("underlying-proxy payoff") is **forbidden** as custody PnL and now raises `CustodyMetricError` ([`custody/pnl.py`](custody/pnl.py)).

## Train/research vs train/custody vs validation/eval/custody

- **research / underlying-proxy** → `eval-data-v2` / `eval-data-v1` (underlying-only K-lines; offline alpha research and `custody replay`). Keep available; this is **not** custody train and **not** the custody validation set.
- **train/custody** → the real **paired** starter `custody-train-2026-09-08_11` (4 recent sessions × US.SPY/US.QQQ/US.AAPL, same-day **underlying 1m + near-ATM option 1m**), built once from read-only OpenD with `python3 -m custody fetch-train`.
- **validation/eval/custody** → frozen `custody-eval-2026-09-14` slice (same-day **underlying 1m + option 1m** OHLCV for three real 2026-09-14 jobs: QQQ LONG, SKHY SHORT, BABA LONG), replayed through the shared market-data provider. See [custody/README.md](custody/README.md).

Both custody train and validation are **paired**: every case carries the same-day underlying 1m for timing and the option 1m for fills. `eval-data-v2` is underlying-only, so it **cannot** be custody train; the offline loaders (`assert_paired_slice`, `require_paired_bars`) and `assert_custody_role` refuse it.

The must-trade custody product must complete exactly one entry+exit per day; the retained research gates can `no_entry` all day and are **not** the custody eval success criterion. Real OpenD market data only — never synthetic.

```bash
python3 -m custody fetch-eval --out /path/to/custody-eval-2026-09-14   # read-only OpenD, once
python3 -m custody eval-session --slice /path/to/custody-eval-2026-09-14
python3 -m custody fetch-train --out out/custody-train-2026-09-08_11    # read-only OpenD, once
python3 -m custody eval-session --slice out/custody-train-2026-09-08_11 --out /tmp/train-report.json
```


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

The new module includes a persistent one-job-per-underlying/day lock, order intents, partial-fill and cancellation handling, restart reconciliation boundaries, a paper lifecycle demo, and registry-driven real Release regression. No live broker order connector is bundled or activated. A read-only OpenD market path is bundled for **dryrun**: `python3 -m custody dryrun ...` resolves the exact contract, polls underlying + option quotes via `OpenQuoteContext`, advances the controller and logs order intents while a broker-less controller and a `dryrun`-mode service guarantee no `place_order`/`unlock_trade` call. Optional WxPusher phone alerts (`--wxpusher-spt` / `CUSTODY_WXPUSHER_SPT`) report each `order_intent` without ever submitting one. See [custody/README.md](custody/README.md) for the `US.SKHY260918P175000` example and the no-secret-commit policy.

## Offline aggressive timing research

[Research source, exact configuration and reproduction commands](research/aggressive_payoff/README.md).

The retained 1m research configuration has underlying payoff27.90 across the fixed83-session eval-data-v2 slice at2bps friction. This is a posthoc **underlying-proxy** research result: **not true option PnL and never a custody metric**. The full report includes ablations, execution sensitivity and the historical5m comparison. This module disables networking and does not enable live trading.

The [5m follow-up](research/aggressive_payoff/five_minute_followup/REPORT.md) tests398 additional configurations: its grid winner has payoff17.22 and the highest subsequent ablation18.45. The1m27.90 configuration stays retained; the5m middle/final periods remain weaker. Exact5m rules and reproducible evidence are included.

## License
Code: MIT. Release market data is a small personal research extract for reproducible offline tests.
