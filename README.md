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

The first fixed must-trade baseline is **`custody_trend_1m_v1`**. See
[`custody/baselines/v1/README.md`](custody/baselines/v1/README.md) for the pinned
paired Releases, full-case option OHLCV replay, empirical payoff ratios,
ablations and frozen validation. Run it with `python3 -m custody baseline`.
It is dryrun-only and does not place broker orders.

The custody bot separates three layers:

- **input** — an **exact option contract** (nearest heavy-theta expiry, near-ATM / not deep OTM; need not be strict 0DTE);
- **signals** — the **same-day underlying 1m K-line**, watched for entry/exit *timing* only;
- **PnL** — the **option path / fills only**. An underlying return × multiplier ("underlying-proxy payoff") is **forbidden** as custody PnL and now raises `CustodyMetricError` ([`custody/pnl.py`](custody/pnl.py)).

## Train/research vs train/custody vs validation/eval/custody

- **research / underlying-proxy** → `eval-data-v2` / `eval-data-v1` (underlying-only K-lines; offline alpha research and `custody replay`). Keep available; this is **not** custody train and **not** the custody validation set.
- **train/custody (canonical)** → the frozen **true-0DTE** paired `custody-train-0dte` slice at `/workspace/pi-jobs/custody-train-0dte/out/custody-train-0dte` (zip + `.sha256` beside it): 14 sessions 2026-08-17 → 2026-09-14, **36** paired cases, **every case `expiry == trade_date` (DTE 0; no DTE 1–4 padding)**, max **DTE 0**, **10 underlyings** — index ETFs SPY 12 / QQQ 5 / IWM 2 (19) and single names AAPL 5 / MSFT 4 / META 4 / NVDA 1 / TSLA 1 / AMD 1 / MU 1 (**17; 47.2% non-ETF**). **Two-tier volume floor** (index **57,000 contracts/day**; single names **5,000/day**) keeps Mag7 below SPY-sized volume, and a **SPY share cap of 35%** (actual **33.3%**) holds out the 6 lowest-volume SPY sessions (recorded in `spy_cap_dropped`). Zip SHA256 `65398673c6617ef0a013c1795936016404babe4a1d4fc1777e16502c2621c7d3`. Underlying 1m is reused from the v2 Release through 2026-09-11 (read-only OpenD for 2026-09-14); option legs are read-only OpenD. **OpenD retention is the limit:** brand-new option expiry groups are refused (quota 60/60) and AMZN/GOOGL have **no warm same-day group**, so they are recorded as `unavailable_symbols`; no DTE≥1 contract is substituted for a missing 0DTE group. Replay with `python3 -m custody eval-session --slice /workspace/pi-jobs/custody-train-0dte/out/custody-train-0dte`.
- **train/custody archive (mixed DTE, NOT formal train)** → the former `custody-train-dte4` slice at `/workspace/pi-jobs/custody-train-dte4/out/custody-train-dte4` (19 sessions 2026-08-17 → 2026-09-11, **124** cases, 36 true 0DTE, max **DTE 4**, zip SHA256 `d72e190e6467f780782f809302a51f9dc8d6f72a00885c2dfb1aa96bf65c51f0`) is demoted to archive because non-0DTE and 0DTE trade very differently. Kept for reference only, not cited as canonical train.
- **raw/opend cache (NOT train)** → `custody-train-v2window-paired` (830 cases, DTE 0…99, zip SHA256 `b3536cb6a1e9eb00103cbb43442b3101679e879f3fe2b6db23dd4e490810a8c6`), the parent option cache. Built with `python3 -m custody fetch-train --out out/custody-train-v2window-paired --v2-dir <eval-data-v2>`. Long-DTE rows are a data-availability artifact; **do not cite as train or alpha evidence**.
- **train/custody-starter** → the tiny paired `custody-train-2026-09-08_11` (4 sessions × US.SPY/US.QQQ/US.AAPL = 12 cases). Plumbing/measurement starter only, **not** the main train set. Built with `python3 -m custody fetch-train-starter`.
- **validation/eval/custody** → frozen `custody-eval-2026-09-14` slice (same-day **underlying 1m + option 1m** OHLCV for three real 2026-09-14 jobs: QQQ LONG, SKHY SHORT, BABA LONG; **flag:** SKHY/BABA are 4-DTE, only QQQ is 0DTE), replayed through the shared market-data provider. See [custody/README.md](custody/README.md).

Both custody train and validation are **paired**: every case carries the same-day underlying 1m for timing and the option 1m for fills. `eval-data-v2` is underlying-only, so it **cannot** be custody train by itself; the offline loaders (`assert_paired_slice`, `require_paired_bars`) and `assert_custody_role` refuse it.

**Grow the formal train while chains exist:** freeze each new session's **same-day (0DTE)** paired underlying+option 1m bars the same day and append them to the canonical set; OpenD drops old weeklies and its option quota blocks new expiry groups, so the 0DTE set only grows with a daily freeze (see `CANONICAL_TRAIN_DIR` / `PROSPECTIVE_FREEZE_NOTE` in [`custody/train_window.py`](custody/train_window.py) and [`custody/train_0dte.py`](custody/train_0dte.py)).

The must-trade custody product must complete exactly one entry+exit per day; the retained research gates can `no_entry` all day and are **not** the custody eval success criterion. Real OpenD market data only — never synthetic.

```bash
python3 -m custody fetch-eval --out /path/to/custody-eval-2026-09-14   # read-only OpenD, once
python3 -m custody eval-session --slice /path/to/custody-eval-2026-09-14
# canonical formal true-0DTE train (frozen): replay only, no fetch
python3 -m custody eval-session --slice /workspace/pi-jobs/custody-train-0dte/out/custody-train-0dte --out /tmp/train-report.json
# build the true-0DTE train (read-only OpenD, quota-aware)
python3 -m custody fetch-train-0dte --out out/custody-train-0dte --v2-dir /path/to/opend_us_options_eval_v2
# raw/parent cache rebuild (read-only OpenD, once)
python3 -m custody fetch-train --out out/custody-train-v2window-paired --v2-dir /path/to/opend_us_options_eval_v2
python3 -m custody fetch-train-starter --out out/custody-train-2026-09-08_11   # tiny plumbing starter, once
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

## 托管期权择时 v2（训练候选，仅 dryrun）

31,295 组参数搜索的两个候选、124 个训练 case 的期权收益、成本/延迟压力测试及复现命令见 [研究报告](research/custody_v2/README.md)。进取型与稳健型分别注册为 `custody_payoff_aggressive_1m_v2`、`custody_payoff_1m_v2`；不改变既有默认策略，不代表样本外验证通过。
