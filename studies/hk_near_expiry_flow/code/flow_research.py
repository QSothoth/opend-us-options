#!/usr/bin/env python3
"""Offline research harness for the HK near-expiry option-flow study.

Reads only the frozen CSVs in ../dataset (no OpenD, standard library only) and
evaluates owner-day candidate rules on market-adjusted forward stock returns.

    python3 code/flow_research.py            # all pre-registered candidates
    python3 code/flow_research.py --csv out.csv
"""
import argparse
import collections
import csv
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET = os.path.normpath(os.path.join(HERE, os.pardir, "dataset"))
MINIMAX = "HK.00100"
MAX_DTE = 14
PERMUTATIONS = 20000
SEED = 20260920


def _num(value):
    if value is None or value in ("", "N/A"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load_klines(path):
    bars = collections.defaultdict(dict)
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            bars[row["code"]][row["time_key"][:10]] = row
    return bars


def load_rank(path, bars, side="CALL"):
    """Owner-days of near-expiry rows on one side, plus every rank row by contract/day.

    The prior-day lookup needs the *full* file: a contract at 15 DTE yesterday is
    near-expiry today.
    """
    owner_days = collections.defaultdict(list)
    by_contract = collections.defaultdict(dict)
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            by_contract[row["code"]][row["trading_date"]] = row
            if side != "ALL" and row["option_type"] != side:
                continue
            dte = _num(row["dte"])
            if dte is None or not 0 <= dte <= MAX_DTE:
                continue
            if row["owner"] not in bars:
                continue
            owner_days[(row["trading_date"], row["owner"])].append(row)
    return owner_days, by_contract


class Panel:
    """Trading-day grid plus market-adjusted forward returns."""

    def __init__(self, bars):
        self.bars = bars
        self.days = sorted({d for code in bars for d in bars[code]})
        self.index = {d: i for i, d in enumerate(self.days)}
        self.market = {}
        for horizon in (1, 2, 3):
            for day in self.days:
                rets = [self.raw(c, day, horizon) for c in bars]
                rets = [r for r in rets if r is not None]
                if rets:
                    self.market[(day, horizon)] = statistics.mean(rets)

    def raw(self, code, day, horizon):
        i = self.index.get(day)
        if i is None or i + horizon > len(self.days) - 1:
            return None
        start = self.bars[code].get(self.days[i])
        end = self.bars[code].get(self.days[i + horizon])
        if not start or not end:
            return None
        return float(end["close"]) / float(start["close"]) - 1

    def excess(self, code, day, horizon):
        raw = self.raw(code, day, horizon)
        factor = self.market.get((day, horizon))
        if raw is None or factor is None:
            return None
        return raw - factor

    def day_change(self, code, day):
        bar = self.bars[code].get(day)
        if not bar:
            return None
        last_close = _num(bar["last_close"])
        if not last_close:
            return None
        return float(bar["close"]) / last_close - 1


def _clip(value, limit):
    return max(-limit, min(limit, value))


def build_features(owner_days, panel, by_contract, baseline_days=5):
    """One record per owner-day, with only close-of-signal-day information."""
    turnover_by_owner = collections.defaultdict(dict)
    for (day, owner), rows in owner_days.items():
        turnover_by_owner[owner][day] = sum(_num(r["turnover"]) or 0.0 for r in rows)

    records = {}
    for (day, owner), rows in owner_days.items():
        turnover = sum(_num(r["turnover"]) or 0.0 for r in rows)
        lead = max(_num(r["turnover"]) or 0.0 for r in rows)

        i = panel.index.get(day)
        prior = None
        if i is not None and i >= baseline_days:
            window = panel.days[i - baseline_days:i]
            history = turnover_by_owner[owner]
            prior = statistics.mean(history.get(d, 0.0) for d in window)
        rel_flow = turnover / prior if prior else None

        weighted, weights = 0.0, 0.0
        for r in rows:
            oi, vol, trn = _num(r["open_interest"]), _num(r["volume"]), _num(r["turnover"])
            if oi and oi > 0 and vol is not None and trn:
                weighted += trn * (vol / oi)
                weights += trn
        voi = weighted / weights if weights else None

        otm, delta_known = 0.0, 0.0
        for r in rows:
            delta, trn = _num(r["delta"]), _num(r["turnover"]) or 0.0
            if delta is None:
                continue
            delta_known += trn
            if delta <= 0.25:
                otm += trn
        otm_share = otm / delta_known if delta_known else None

        # R2: price impact of the flow rather than its size.
        prev_day = panel.days[i - 1] if i else None
        iv_w = iv_wt = 0.0
        unexp_w = unexp_wt = 0.0
        resid_w = resid_wt = 0.0
        fresh = fresh_wt = 0.0
        dte2 = dte5 = 0.0
        window_codes = set()
        if i is not None and i >= baseline_days:
            for d in panel.days[i - baseline_days:i]:
                for code, per_day in by_contract.items():
                    if d in per_day:
                        window_codes.add(code)
        chg_abs = None
        bar = panel.bars[owner].get(day)
        if bar:
            last_close = _num(bar["last_close"])
            if last_close:
                chg_abs = float(bar["close"]) - last_close

        for r in rows:
            trn = _num(r["turnover"]) or 0.0
            row_dte = _num(r["dte"])
            if row_dte is not None and row_dte <= 2:
                dte2 += trn
            if row_dte is not None and row_dte <= 5:
                dte5 += trn
            prev = by_contract.get(r["code"], {}).get(prev_day) if prev_day else None
            iv_now, iv_prev = _num(r["iv"]), _num(prev["iv"]) if prev else None
            if iv_now is not None and iv_prev is not None and trn:
                iv_w += trn * _clip(iv_now - iv_prev, 50.0)
                iv_wt += trn
            delta, price, chg = _num(r["delta"]), _num(r["option_price"]), _num(r["change_ratio"])
            theta = _num(r["theta"])
            if None not in (delta, price, chg, chg_abs) and chg > -100 and trn:
                prev_price = price / (1 + chg / 100.0)
                if prev_price > 0:
                    unexp_w += trn * _clip(chg - 100.0 * delta * chg_abs / prev_price, 200.0)
                    unexp_wt += trn
                    if theta is not None:
                        resid_w += trn * _clip(
                            chg - 100.0 * (delta * chg_abs + theta) / prev_price, 200.0)
                        resid_wt += trn
            if window_codes:
                fresh_wt += trn
                if r["code"] not in window_codes:
                    fresh += trn

        records[(day, owner)] = {
            "trading_date": day,
            "owner": owner,
            "n_contracts": len(rows),
            "turnover": turnover,
            "lead_turnover": lead,
            "rel_flow": rel_flow,
            "voi": voi,
            "otm_share": otm_share,
            "concentration": lead / turnover if turnover else None,
            "day_chg": panel.day_change(owner, day),
            "min_dte": min(_num(r["dte"]) for r in rows),
            "iv_change": iv_w / iv_wt if iv_wt else None,
            "unexplained": unexp_w / unexp_wt if unexp_wt else None,
            "residual": resid_w / resid_wt if resid_wt else None,
            "fresh_share": fresh / fresh_wt if fresh_wt else None,
            "dte2_share": dte2 / turnover if turnover else None,
            "dte5_share": dte5 / turnover if turnover else None,
        }

    # E2 is cross-sectional: the day's own top quintile by residual.
    by_day = collections.defaultdict(list)
    for key, rec in records.items():
        if rec["residual"] is not None:
            by_day[rec["trading_date"]].append(key)
    for day, keys in by_day.items():
        keys.sort(key=lambda k: records[k]["residual"], reverse=True)
        cut = max(1, round(len(keys) * 0.20))
        for rank, key in enumerate(keys):
            records[key]["residual_top20"] = rank < cut
    return records


# Pre-registered candidates (notes/R1_PREREG.md). Each takes a record and
# returns True/False, or None when the feature cannot be computed.
def _gate(value, test):
    return None if value is None else test(value)


CANDIDATES = [
    ("BASE", "universe: any near-expiry CALL flow", lambda r: True),
    ("REF", "aggressive: lead>=200k and sum>=1M", lambda r: r["lead_turnover"] >= 200000 and r["turnover"] >= 1000000),
    ("C1", "rel_flow >= 3x own 5-day mean", lambda r: _gate(r["rel_flow"], lambda v: v >= 3.0)),
    ("C2", "turnover-weighted volume/OI >= 1.0", lambda r: _gate(r["voi"], lambda v: v >= 1.0)),
    ("C3", "delta<=0.25 share >= 50%", lambda r: _gate(r["otm_share"], lambda v: v >= 0.50)),
    ("C4", "lead contract share >= 70%", lambda r: _gate(r["concentration"], lambda v: v >= 0.70)),
    ("C5", "C1 and underlying closed up", lambda r: None if r["rel_flow"] is None or r["day_chg"] is None
     else (r["rel_flow"] >= 3.0 and r["day_chg"] >= 0)),
    ("D1", "same-contract IV change >= +3 vol pts", lambda r: _gate(r["iv_change"], lambda v: v >= 3.0)),
    ("D2", "delta-unexplained option return >= +10 pts", lambda r: _gate(r["unexplained"], lambda v: v >= 10.0)),
    ("D3", "fresh-contract turnover share >= 50%", lambda r: _gate(r["fresh_share"], lambda v: v >= 0.50)),
    ("D4", "D1 and D3", lambda r: None if r["iv_change"] is None or r["fresh_share"] is None
     else (r["iv_change"] >= 3.0 and r["fresh_share"] >= 0.50)),
    ("D5", "DTE<=2 turnover share >= 50%", lambda r: _gate(r["dte2_share"], lambda v: v >= 0.50)),
    ("E1", "delta+theta residual >= 0", lambda r: _gate(r["residual"], lambda v: v >= 0.0)),
    ("E2", "residual in the day's top quintile", lambda r: r.get("residual_top20")),
    ("E3", "E2 and DTE<=5 share >= 50%", lambda r: None if r.get("residual_top20") is None
     or r["dte5_share"] is None else (r["residual_top20"] and r["dte5_share"] >= 0.50)),
]


def evaluate(records, panel, predicate, horizon, permutations=PERMUTATIONS, seed=SEED):
    """Mean excess return of selected owner-days, with a within-day permutation test."""
    pool = collections.defaultdict(list)  # day -> [(key, excess)] over eligible records
    picked = []
    for key, rec in sorted(records.items()):
        verdict = predicate(rec)
        if verdict is None:
            continue
        excess = panel.excess(rec["owner"], rec["trading_date"], horizon)
        if excess is None:
            continue
        pool[rec["trading_date"]].append((key, excess))
        if verdict:
            picked.append((key, excess))
    if not picked:
        return None

    values = [e for _, e in picked]
    observed = statistics.mean(values)
    counts = collections.Counter(day for (day, _), _ in picked)

    rng = random.Random(seed)
    tol = 1e-12 * max(1.0, abs(observed))  # permuted sums differ from observed only by float order
    ge = le = 0
    for _ in range(permutations):
        total, n = 0.0, 0
        for day, k in counts.items():
            bucket = [e for _, e in pool[day]]
            sample = bucket if k >= len(bucket) else rng.sample(bucket, k)
            total += sum(sample)
            n += len(sample)
        mean = total / n
        ge += mean >= observed - tol
        le += mean <= observed + tol
    p_two = min(1.0, 2 * min(ge, le) / permutations)

    ex_mm = [e for (day, owner), e in picked if owner != MINIMAX]
    by_day = collections.defaultdict(list)
    for (day, _), e in picked:
        by_day[day].append(e)

    # Leave-one-day-out: the drop-one mean that is weakest for the claim, i.e.
    # closest to zero. Defined this way it stays meaningful when the total is small.
    weakest_mean, weakest_day = None, None
    if len(by_day) > 1:
        for day in by_day:
            rest = [e for (d, _), e in picked if d != day]
            if not rest:
                continue
            mean = statistics.mean(rest)
            if weakest_mean is None or abs(mean) < abs(weakest_mean):
                weakest_mean, weakest_day = mean, day

    return {
        "n": len(values),
        "days": len(by_day),
        "owners": len({o for (_, o), _ in picked}),
        "win_rate": sum(1 for v in values if v > 0) / len(values),
        "mean": observed * 100,
        "median": statistics.median(values) * 100,
        "p_two_sided": p_two,
        "n_ex_minimax": len(ex_mm),
        "mean_ex_minimax": statistics.mean(ex_mm) * 100 if ex_mm else None,
        "loo_day_mean": weakest_mean * 100 if weakest_mean is not None else None,
        "loo_day": weakest_day,
    }


def spearman(pairs):
    """Rank correlation, average ranks for ties."""
    if len(pairs) < 3:
        return None
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            average = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                out[order[k]] = average
            i = j + 1
        return out
    xr = ranks([x for x, _ in pairs])
    yr = ranks([y for _, y in pairs])
    mx, my = statistics.mean(xr), statistics.mean(yr)
    num = sum((a - mx) * (b - my) for a, b in zip(xr, yr))
    den = (sum((a - mx) ** 2 for a in xr) * sum((b - my) ** 2 for b in yr)) ** 0.5
    return num / den if den else None


def diagnostics(records, panel):
    """Post-hoc descriptions. Not candidates: they take no part in the R1/R2 verdict."""
    print("\n-- feature vs T+1 excess return, Spearman over the whole universe --")
    for feature in ("turnover", "rel_flow", "voi", "otm_share", "concentration",
                    "iv_change", "unexplained", "fresh_share", "dte2_share"):
        pairs = []
        for rec in records.values():
            value = rec.get(feature)
            excess = panel.excess(rec["owner"], rec["trading_date"], 1)
            if value is not None and excess is not None:
                pairs.append((value, excess))
        rho = spearman(pairs)
        print(f"   {feature:14s} n={len(pairs):4d} rho={'n/a' if rho is None else f'{rho:+.3f}'}")

    print("\n-- 'traded above the signal close within T+1..T+3' base rate --")
    def max_up_rate(keys):
        hit = total = 0
        for day, owner in keys:
            i = panel.index.get(day)
            if i is None or i + 3 > len(panel.days) - 1:
                continue
            start = panel.bars[owner].get(panel.days[i])
            highs = [panel.bars[owner].get(panel.days[i + k]) for k in (1, 2, 3)]
            if not start or any(h is None for h in highs):
                continue
            total += 1
            hit += max(float(h["high"]) for h in highs) > float(start["close"])
        return hit, total
    flow_keys = list(records)
    all_keys = [(d, c) for c in panel.bars for d in panel.bars[c]]
    for label, keys in (("near-expiry CALL owner-days", flow_keys), ("every panel stock-day", all_keys)):
        hit, total = max_up_rate(keys)
        if total:
            print(f"   {label:28s} n={total:5d} hit={hit / total:.0%}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default=DATASET)
    ap.add_argument("--side", default="CALL", choices=("CALL", "PUT", "ALL"),
                    help="option side the candidates run on (the rank file may hold both)")
    ap.add_argument("--permutations", type=int, default=PERMUTATIONS)
    ap.add_argument("--csv", help="write the result table here")
    ap.add_argument("--diagnostics", action="store_true", help="also print the post-hoc descriptions")
    args = ap.parse_args(argv)

    bars = load_klines(os.path.join(args.dataset, "klines_day.csv"))
    owner_days, by_contract = load_rank(
        os.path.join(args.dataset, "hk_option_rank_raw.csv"), bars, args.side)
    panel = Panel(bars)
    records = build_features(owner_days, panel, by_contract)

    print(f"side={args.side} owner-days={len(records)} owners={len({o for _, o in records})} "
          f"days={len({d for d, _ in records})} panel_days={len(panel.days)}")

    out = []
    for cid, label, predicate in CANDIDATES:
        for horizon in (1, 2):
            res = evaluate(records, panel, predicate, horizon, args.permutations)
            if res is None:
                print(f"{cid} T+{horizon}: no signals")
                continue
            row = {"id": cid, "rule": label, "horizon": f"T+{horizon}", **res}
            out.append(row)
            loo = "n/a" if res["loo_day_mean"] is None else f"{res['loo_day_mean']:+.2f}%"
            ex_mm = "n/a" if res["mean_ex_minimax"] is None else f"{res['mean_ex_minimax']:+.2f}%"
            print(f"{cid:5s} T+{horizon}  n={res['n']:4d} days={res['days']:2d} owners={res['owners']:2d} "
                  f"wr={res['win_rate']:.0%} exc_mean={res['mean']:+.2f}% med={res['median']:+.2f}% "
                  f"p={res['p_two_sided']:.3f} ex-MM={ex_mm}(n={res['n_ex_minimax']}) loo={loo}")

    if args.diagnostics:
        diagnostics(records, panel)

    if args.csv and out:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(out[0]))
            writer.writeheader()
            writer.writerows(out)
        print(f"wrote {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
