# Custody OHLCV baseline v1

## Scope

First fixed baseline, not a parameter-search winner or a claim of robust alpha.
Every supplied contract/session is processed; no skipped signal cases. Long
calls and long puts both have PnL `(exit premium - entry premium) * quantity *
multiplier`. Each case uses one contract; totals are not a compounded portfolio.
No OpenD connection or real order was used for development or evaluation.

## Data

- Main source: `daad65c2a573fa078e9500022df989f0ce105bdc`.
- Train: Release `custody-train-dte4`, 124 paired cases over 19 ET sessions,
  2026-08-17 through 2026-09-11. SHA256
  `d72e190e6467f780782f809302a51f9dc8d6f72a00885c2dfb1aa96bf65c51f0`.
- Validation: Release `custody-eval-2026-09-14`, 3 cases. SHA256
  `fd7c92bbada5cf854564a3f12b6db3d581ed34c8c524e525b15d78ef412a7160`.
- Verify the ZIPs, then extract. The runner also checks packaged file checksums.
  Only listed case/session pairs are replayed even when a CSV contains extra days.
- Train case direction was selected by the source package's scenario labels.
  Labels never reach indicators. Findings are conditional on the supplied
  contracts/directions and are not evidence of prospective direction selection.
- No historical bid/ask is available. OHLCV fills and all spread/slippage stress
  results are simulations. A bar close is not proof of an executable limit order.

## Fixed policy (chosen before inspecting training PnL)

- Input: the exact contract, underlying and LONG/SHORT; DTE 0–4, dryrun-only.
- Features: current-session OR15/VWAP, EMA 9/21, RSI 14, explicit rolling-window
  ADX approximation, and current-volume/prior-20-observed-bars mean volume.
  No prior daily ATR, cross-day RVOL, previous sessions or option price features.
- Entry: at least 15 observed minutes; 4 of 6 votes before 10:00, 3 afterward.
  Forced fallback from 10:30. A missing/stale quote can delay the execution
  intent; it does not create a successful no-trade case.
- Exits: loss-side failed followthrough after 20 minutes plus 3 contrary
  VWAP/EMA frames; 6-entry-ATR safety stop; an 8-entry-ATR trail activated after
  6-entry-ATR progress; 15:45 ET flatten signal, or 15 minutes before early close.
  Intraday ATR14 is frozen at entry intent. Profits have no fixed target.
- Signal decisions use completed underlying bars. A previously queued intent
  fills at a subsequent positive-volume option close, at least one minute later.
  New decisions never fill on the same bar. The reference-price intent is
  explicitly simulated as repriced, not falsely treated as a filled resting limit.
- Zero-volume bars do not establish executions; missing exits are not backfilled
  or scored as zero PnL. All execution must finish before underlying session close;
  option prints after 16:00 are not used.
- Underlying safety and routine exits for v1 use 1m closes only. Bid/ask spread
  filters are removed from exit eligibility; quote identity/freshness remains.

## Reproduction (Python 3.12, standard library only for the baseline)

```bash
python3 -m unittest custody.tests.test_baseline custody.tests.test_runtime custody.tests.test_dryrun -q
python3 -m custody baseline --slice /path/to/custody-train-dte4 \
  --out /tmp/train.json --variants baseline
# Predeclared diagnostics; incomplete counterfactual cases return exit code 2.
python3 -m custody baseline --slice /path/to/custody-train-dte4 \
  --out /tmp/train-all.json --variants baseline fixed_time fixed_entry_same_exit indicator_entry_hold_to_close
python3 -m custody baseline --slice /path/to/custody-eval-2026-09-14 \
  --out /tmp/validation.json --variants baseline fixed_time --freeze custody/baselines/v1/FROZEN.json
```

`protocol.json` predates training PnL. `FROZEN.json` pins the strategy and all
timing, runtime and replay code before opening held-out bars. Holdout replay
requires the frozen files and permits only the baseline plus its predeclared
fixed-time benchmark. Validation was not used to tune any parameter.

The two counterfactuals isolate entry timing and profit-running exits. Each has
its own configuration hash; none silently changes the frozen baseline identity.
The fixed-time benchmark means a 10:00 eligibility signal, with an intent/fill
later if the option has no positive-volume observation then.

## Results and conventions

See `train_report.json`, `validation_report.json` and `summary.json`. Every report
includes case-level signal/fill timestamps, exact contracts, entry/exit reasons,
premium return and dollar PnL, counts and failures. Payoff is average winning
trade / absolute average losing trade; profit factor is total wins / total losses.
Both return-based and one-contract-dollar versions are reported, with flat trades
retained in the win-rate denominator. Undefined ratios are null, not infinity.

Primary OHLCV numbers have zero assumed commission/slippage. Sensitivities use
$0.65 per contract per side and 0/25/50/100 bps per side of option premium;
these are disclosed stress assumptions, not measured historical costs. Failing
cases remain in coverage and prevent publishing a full-cohort total. Matched-case
comparisons are labeled separately from the full 124-case baseline.

Resident `custody dryrun` remains intent-only; offline `custody baseline` exercises
the same service with internal simulation events. It does not certify broker
queue position, NBBO execution, retries or live fill probability.
