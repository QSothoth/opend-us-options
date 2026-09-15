# Baseline v1 result

All amounts below simulate one contract per case from observed option OHLCV, with zero primary fees/slippage. No broker orders.

| Set | Complete | Win rate | Payoff (USD) | Payoff (premium returns) | Mean premium return | Total USD |
|---|---:|---:|---:|---:|---:|---:|
| Train | 124/124 | 22.58% | 3.98 | 3.63 | +0.99% | +1,526 |
| Frozen validation | 3/3 | 33.33% | 0.74 | 1.49 | -3.04% | -75 |
| Validation fixed-time benchmark | 3/3 | 100% | undefined (no losses) | undefined | +65.10% | +425 |

Validation per contract: QQQ call -$24 (-14.12%); SKHY put -$95 (-21.35%); BABA call +$44 (+26.35%). The baseline was not retuned after this result.

Training fee-only assumption ($0.65 per side) leaves +$1,364.80 and +0.247% mean premium return. Adding 25 bps slippage per side leaves +$1,021.90 but mean premium return becomes -0.255%. These different aggregations must not be conflated. At 100 bps per side plus fees total PnL is -$6.82. Costs are sensitivity assumptions, not reconstructed historical quotes.

The largest dollar winner is +$1,570; removing it changes full-train PnL to -$44. The best percentage trade is +536.44%. Average hold is 121.8 minutes; 12 entries used deadline fallback.

The fixed-time and hold-to-close training diagnostics each lack one valid exit (META 2026-08-26), so their full 124-case total is null. `summary.json` separately compares the same 123 completed cases. No exit price was backfilled.

64 synthetic/runtime/dryrun tests passed. Two full training replays agreed on v1 trade outcomes. The first validation attempt stopped at metadata verification because its checksum map uses csv/parquet keys; a format-only fix was tested and re-frozen before any validation bars were consumed. `FROZEN.initial.json` and `FROZEN.json` preserve the audit trail. The complete frozen baseline plus predeclared fixed-time benchmark was then evaluated once.

This is a reproducible reference, not evidence of stable superiority. The train direction labels are conditional case definitions; the 19-day train and 3-case validation cannot establish broad generalization.
