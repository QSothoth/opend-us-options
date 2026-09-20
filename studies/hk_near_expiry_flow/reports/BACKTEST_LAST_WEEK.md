# HK option-flow backtest — week of 2026-09-14 (MiniMax lead)

Replay of Futu OpenD `get_option_event(OptionMarket.HK_SECURITY)` against last week's listed-equity-option unusual prints. **Read-only** (`OpenQuoteContext` only; no trade context, unlock, or `place_order`).

Run (2026-09-20, after the Friday 9/18 close):

```bash
/home/box/futu/venv/bin/python3 /home/box/futu/option_flow/scan_hk_option_flow.py --backtest \
    --min-turnover 200000 --max-dte 5 \
    --cluster-window-min 30 --cluster-min-prints 3 --cluster-min-turnover 1500000 \
    --max-day-num 7 --count 300 --pages 5 \
    --out-dir /workspace/hk-option-flow
```

Artifacts:

| File | Rows | What |
|---|---|---|
| `hk_option_events_raw.csv` | 244 | All CALL BUY events OpenD returned for `MAX_DAY_NUM=7` |
| `hk_option_alerts.csv` | 25 | Prints that pass the size **or** cluster rule with computed DTE ≤ 5 |
| `hk_option_daily_rollup.csv` | 13 | Per-day owner totals of near-expiry CALL BUY flow |

Fill window in the feed: **2026-09-11 09:30:00 → 2026-09-18 15:56:52**. Connection succeeded (`ret=0`, `all_count=244`).

DTE is computed from `strike_time` / `YYMMDD` in `option_code` versus the `fill_time` **date**. OpenD’s `dte` column is ignored: queried on 9/20, a 9/18 expiry shows `dte = -2`.

## Verdict: MiniMax 9/16 morning **would have fired**

**Yes.** Default rules alert on the 2026-09-16 morning CALL BUY cluster in `HK.MNX260918C235000` (`MNX260918C235`, strike 235, expiry 2026-09-18, **2 DTE at fill**).

Seven aggressive BUY prints in **12.6 minutes**, **10,690** contracts, **3,615,686 HKD** turnover. Every print is above the 200k size floor **and** the burst qualifies as a cluster (`7 ≥ 3` prints and `3.62M ≥ 1.5M` HKD).

| fill_time (HKT) | option_code | px | vol | turnover HKD | underlying |
|---|---|---|---|---|---|
| 2026-09-16 09:47:50 | HK.MNX260918C235000 | 17.48 | 1,000 | 349,600 | 247.2 |
| 2026-09-16 09:53:33 | HK.MNX260918C235000 | 15.17 | 840 | 254,856 | 244.0 |
| 2026-09-16 09:56:18 | HK.MNX260918C235000 | 14.53 | 2,000 | 581,200 | 243.4 |
| 2026-09-16 09:56:29 | HK.MNX260918C235000 | 14.19 | 1,350 | 383,130 | 242.6 |
| 2026-09-16 09:58:31 | HK.MNX260918C235000 | 18.07 | 1,500 | 542,100 | 248.2 |
| 2026-09-16 09:59:44 | HK.MNX260918C235000 | 18.45 | 2,000 | 738,000 | 249.2 |
| 2026-09-16 10:00:27 | HK.MNX260918C235000 | 19.17 | 2,000 | 766,800 | 249.0 |

Cluster id: `HK.00100|2026-W38|2026-09-16|1`  
Alert reason on all seven: `size+cluster`.

This is the known lead into the 9/17–18 cash-session surge. MiniMax (`HK.00100`) daily bars from the same OpenD quote session:

| date | open | high | low | close | vs prior close |
|---|---|---|---|---|---|
| 2026-09-14 | 255.0 | 258.8 | 249.0 | 252.4 | −6.52% |
| 2026-09-15 | 256.0 | 259.2 | 231.4 | 234.6 | −7.05% |
| 2026-09-16 | 236.6 | 251.8 | 233.0 | 238.0 | **+1.45%** |
| 2026-09-17 | 242.0 | 255.8 | 242.0 | 254.8 | **+7.06%** |
| 2026-09-18 | 264.8 | 303.0 | 263.6 | 303.0 | **+18.92%** |

On 9/16 the CALL BUYs printed while the stock was already bouncing (underlying 242.6–249.2 vs prior close 234.6, about +3.4% to +6.2%). The session faded to 238 into the close, then 9/17–18 delivered the real move. `--require-stock-not-up 8` would **still** have kept this cluster.

## MiniMax context 9/15–9/18 (and the 9/14 precursor)

Only **three** qualifying clusters in the whole 7-day CALL BUY tape — **all MiniMax, all the 9/18 weekly**:

| day | contract | prints | HKD | span | notes |
|---|---|---|---|---|---|
| 2026-09-14 09:37–09:55 | `MNX260918C270` | 7 | 2,019,500 | 19 min | Size+cluster. Stock still selling off (close 252.4, −6.5%). False lead on direction that day. |
| 2026-09-15 13:36–13:48 | `MNX260918C255` | 4 | 663,820 | 13 min | Cluster-only (3 of 4 prints are **below** 200k; the 4th is 229,920). Stock closed 234.6, −7.1%. |
| 2026-09-16 09:47–10:00 | `MNX260918C235` | 7 | 3,615,686 | 13 min | **The alert that matters.** |

Near-expiry MiniMax CALL BUY turnover by day: 9/14 2.29M → 9/15 0.66M → **9/16 3.62M** → then no near-expiry MiniMax CALL BUY alerts on 9/17–18 (flow rotated out the 9/18 weekly into longer-dated / already-ITM names, which fail `max-dte=5` *at fill*).

## Other HK names that would have alerted (false-positive awareness)

Same rules, same week. These are **single size prints** (≥ 200k HKD), not clusters. Treat them as noise next to MiniMax’s 3.6M burst.

| owner | name | when | contract | HKD | reason |
|---|---|---|---|---|---|
| HK.02513 | Z.AI | 9/14 09:59 | `KAT260918C825` | 244,200 | size |
| HK.00700 | Tencent | 9/15 09:46 | `TCH260918C440` | 299,040 | size |
| HK.09988 | BABA-W | 9/15 11:31 | `ALB260918C110` | 250,000 | size |
| HK.09868 | XPENG-W | 9/15 15:20 | `PEN260918C40500` | 202,920 | size |
| HK.06181 | Laopu Gold | 9/16 13:00 | `LAO260918C375` | 214,500 | size |
| HK.01211 | BYD | 9/17 14:30 | `BYD260918C81` | 239,200 | size |

Near-expiry CALL BUY prints that **did not** alert (turnover &lt; 200k and not in a 3-print / 1.5M cluster): Zhaojin Mining (`HK.01818`), Zijin Gold Intl (`HK.02259`), ZJ Innolight (`HK.03308`).

If the goal is “MiniMax-style” leads, the useful gate is the **cluster** (3+ prints or ≥ 1.5M HKD in 30 minutes), not the 200k single-print size floor. Size-only alerts this week were all one-and-done names.

## Daily rollup — near-expiry CALL BUY turnover (top owner / day)

| day | top owner | prints | HKD |
|---|---|---|---|
| 2026-09-14 | HK.00100 MiniMax | 8 | 2,285,150 |
| 2026-09-15 | HK.00100 MiniMax | 4 | 663,820 |
| 2026-09-16 | HK.00100 MiniMax | 7 | **3,615,686** |
| 2026-09-17 | HK.01211 BYD | 1 | 239,200 |
| 2026-09-18 | HK.03308 ZJ Innolight | 1 | 102,000 |

## What this does *not* prove

- OpenD’s unusual-activity feed is not a full tape; small prints never appear.
- `MAX_DAY_NUM=7` on a Saturday replay covers 9/11–9/18, not a longer archive.
- Warrants are out of scope; this is listed HK equity options only.
- No orders were placed.
