# Aggressive payoff timing — underlying proxy, not true option PnL

This experiment obeys the user's updated objective: maximize payoff ratio and large winning trades, while deferring win-rate gating. There is no PF, win-rate, expectancy, or drawdown filter in selection. All those metrics are still reported. No future confidence gate is implemented or credited with hypothetical benefits.

## Run

Python 3.12, numpy and pandas; Linux/macOS (Windows: WSL). Versions are in `environment.json`. A full run is CPU-only and uses six worker processes by default.

```bash
python3 code/run_all.py --zip /absolute/path/opend_us_options_eval_v2.zip --out reproduced --workers 6
```

Fixed Release: https://github.com/QSothoth/opend-us-options/releases/tag/eval-data-v2

ZIP SHA256: `df93506e498be259a9654c8bf82738aa8ed3604ec2936cf4dfbed0de26aba0c6`

The local ZIP, manifest version, internal checksums, OHLC invariants and trading calendar are verified on each run. The included pinned-schema reader loads the actual Release Parquet files. There is no OpenD, market-data request, or order client, and the evaluation runtime disables TCP/IP and DNS. Raw data are excluded from the deliverable.

The full command includes the staged search, frozen ablations, parameter perturbations, separately labelled posthoc peak diagnostics, and cold verification. Approximately 24 minutes with six processes in the reference environment. For a fast independent replay of the 61 locked candidates and all 20 main reported cases:

```bash
python3 code/verify_frozen.py --zip /absolute/path/opend_us_options_eval_v2.zip --reference results --out verified
```

This does not repeat all 47,531 search configurations. It recomputes the complete final ranking, trade rows, summary metrics and MTM drawdowns with new caches and reversed candidate order. `posthoc_peak_rule.json` is the highest observed payoff in the fixed local perturbations, identified using all83 history; it is explicitly not an independently validated winner and does not replace `recommended_rule.json`.

## Human Job

```bash
python3 code/replay_job.py --zip /absolute/path/opend_us_options_eval_v2.zip --rule results/recommended_rule.json --symbol SPY --direction LONG --out spy_long
```

`JobSpec(symbol, direction, rule, contract=None, max_qty=1, flatten_et='15:45', dry_run=True)` accepts the human's choice. Contract and quantity are interface metadata, not option pricing or leverage. Each symbol+direction+date can abstain or have one entry and one full exit. No reversal, scaling or reentry. Batch directions are counterfactuals, not a portfolio or two daily round trips in one symbol.

## Selection

The complete bounded grid and every stage are frozen in `protocol.py`, before returns. First28 sessions fit all stages; middle28 selects a locked shortlist; final27 evaluates fixed candidates only. Every date was previously studied, so this is not pristine out-of-sample evidence.

A: 11,340 entry specs plus the old 4.45-payoff entry, two exit probes each. B: 468 exit plans plus probes per retained entry. C: initial close-based loss and stall/late/flatten interactions. D: individual relevant parameter neighbors plus two joint grids, once. All selection stages use only first28 until the frozen shortlist reaches validation. Arbitrary parameter expansion after seeing stress results is prohibited. This is not an exhaustive Cartesian product or proof of a global optimum.

The score is `log(min(payoff_2bps,payoff_5bps)) + .20*log(avg_win_pct_2bps/.5)`. Validation emphasizes the weaker of fit/validation and penalizes disagreement. Each block needs >=20 trades, >=4 winners and >=8 losers under each 2/5bps cost, >=8 trading dates and >=5 symbols. These are conditional-mean sample checks, not a fixed win-rate floor. No positive-return requirement is reintroduced.

## Causality and exits

All1/5/15m bars derive from the same real1m input. Native completed closes drive entry and active exits; execution uses the next real1m open. A boundary timestamp can match the native close and next1m open, but never means filling at the signal bar's open. Soft loss, failure and stall exits are checked at closes, never filled retrospectively at intrabar thresholds. Only a previously resting hard safety stop uses 1m high/low and adverse gap opens. Flatten/maxhold clocks are predetermined. Daily ATR10/20/40 uses only completed prior days, with shift1 before session joins. No fixed profit target.

RVOL is current completed native-bar volume versus prior same-clock sessions, minimum five. EMA/RSI/ADX/BB/squeeze/Supertrend-style trends inherit observed RTH history only. The Supertrend-style band is explicitly implemented in `features.py`: HL2 +/- a causal Wilder ATR multiple, recursive final bands and close-based direction flips. It is not claimed to match a proprietary indicator. Impulse uses real body, range/dailyATR and directional close location. Relative strength uses synchronized SPY (SPY itself uses QQQ). No tick-level information, actual aggressor flow, dealer gamma or option-chain data is invented.

## Evidence

`COMPARISON.csv` and `ABLATION.csv` contain whole-period and chronological comparisons. All scenarios, cost0/2/5/10/20bps, signal latency1/2min, symbol/direction and top-winner contributions are reported. Ex-post labels never enter features or selection. Bootstrap intervals resample five-day date blocks, preserving cross-symbol/direction dependence, and are not selection-adjusted.

Ablating a confirmation replaces it with true and lowers quorum by one, so it relaxes that condition. Exit-only ablations verify identical entry fills. Parameters disabled by zero may produce identical ablations; these are not effective discoveries.

The old4.45 version is reproduced before fitting. Tests poison future minute OHLCV and current/future daily values, check all39 indicator/profile gates and selected state prefixes, verify soft exits against completed native closes, compare single Jobs with batch replicas, and reject missing flatten quotes. Code license: MIT; raw market data are not included.
