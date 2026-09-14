#!/usr/bin/env python3
"""Cold replay of frozen shortlist and reported cases, without repeating the search."""
import run as rt
import argparse, json, math, time
from pathlib import Path
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from data import load_release, make_cube, write_json
from engine import aggregate_1m, daily_atr, mtm_drawdown
from protocol import rule_for
from metrics import metric


def compare_dict(actual, expected, scope):
    for key, want in expected.items():
        if key == 'id':
            continue
        got = actual[key]
        if isinstance(want, bool):
            assert got == want, (scope, key, got, want)
        elif want is None:
            assert got is None or not np.isfinite(got), (scope, key, got, want)
        elif isinstance(want, (int, float)):
            assert math.isclose(got, want, rel_tol=1e-12, abs_tol=1e-12), (scope, key, got, want)
        else:
            assert got == want, (scope, key, got, want)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--zip', required=True)
    p.add_argument('--reference', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    start = time.time()
    ref, out = Path(a.reference), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    frames, cal, audit, _ = load_release(a.zip)
    rt.DAY = frames['klines_day']
    rt.CUBES = {s: make_cube(aggregate_1m(frames['klines_1m'], s), cal, s) for s in [1, 5, 15]}
    rt.EXE = rt.CUBES[1]
    rt.ADS = {n: daily_atr(rt.EXE, rt.DAY, n) for n in [10, 20, 40]}
    rt.BANKS = {}
    periods = json.loads((ref / 'data_audit.json').read_text())['periods']
    fit = rt.subset(rt.EXE, np.flatnonzero(rt.EXE['keys'].date.isin(periods['fit28'])))
    val = rt.subset(rt.EXE, np.flatnonzero(rt.EXE['keys'].date.isin(periods['validation28'])))
    shortlist = json.loads((ref / 'shortlist_before_validation.json').read_text())
    locked = json.loads((ref / 'LOCKED_SELECTION_BEFORE_STRESS.json').read_text())
    expected = {c['id']: c for c in locked['ranked_shortlist']}
    reranked = []
    # Reverse order, fresh caches, and no search prewarming address cache/order contamination.
    for c in reversed(shortlist):
        f = rt.evaluate(rt.run_case(c, fit, cache=False)[0])
        v = rt.evaluate(rt.run_case(c, val, cache=False)[0])
        compare_dict(f, expected[c['id']]['fit'], c['id'] + '/fit')
        compare_dict(v, expected[c['id']]['validation'], c['id'] + '/validation')
        stability = abs(np.log(max(f['robust_payoff'], 1e-6) / max(v['robust_payoff'], 1e-6)))
        score = min(f['score'], v['score']) + .25 * (f['score'] + v['score']) / 2 - .1 * stability
        assert math.isclose(score, expected[c['id']]['selection_score'], abs_tol=1e-12)
        reranked.append(dict(id=c['id'], signal_minutes=c['entry']['signal_minutes'],
                             sample_ok=f['sample_ok'] and v['sample_ok'], selection_score=score))
    reranked.sort(key=lambda x: (x['sample_ok'], x['selection_score'], x['id']), reverse=True)
    assert [r['id'] for r in reranked] == [r['id'] for r in locked['ranked_shortlist']]
    for s in [1, 5, 15]:
        got = next(r for r in reranked if r['signal_minutes'] == s)['id']
        want = next(r for r in locked['per_timeframe'] if r['signal_minutes'] == s)['id']
        assert got == want
    print(f'Cold shortlist: {len(shortlist)} fit/validation candidates and complete ranking match', flush=True)
    reported = json.loads((ref / 'reported_configs.json').read_text())
    comparison = pd.read_csv(ref / 'COMPARISON.csv')
    rows, fillchecks = [], {}
    (out / 'trades').mkdir(exist_ok=True)
    rt.BANKS = {}
    for name, c in reversed(list(reported.items())):
        actual, info = rt.run_case(c, record_mtm=True, cache=False)
        actual.to_csv(out / 'trades' / (name + '.csv'), index=False)
        # Compare after identical CSV type inference, retaining nan semantics.
        saved = pd.read_csv(out / 'trades' / (name + '.csv'))
        want = pd.read_csv(ref / 'trades' / (name + '.csv'))
        assert_frame_equal(saved, want, check_exact=False, rtol=1e-12, atol=1e-12)
        fillchecks[name] = rt.verify_fills(actual, rt.EXE, rule_for(c))
        for period, ds in periods.items():
            g = actual[actual.date.isin(ds)]
            dd, _ = mtm_drawdown(g, info['mtm_returns'])
            m = {**metric(g), **rt.extras(g), 'mtm_mdd_pct': dd}
            row = comparison[(comparison.name == name) & (comparison.period == period)].iloc[0]
            for key, got in m.items():
                want_value = row[key]
                if got is None or pd.isna(got):
                    assert pd.isna(want_value), (name, period, key)
                else:
                    assert math.isclose(got, want_value, rel_tol=1e-12, abs_tol=1e-12), (name, period, key, got, want_value)
            rows.append(dict(name=name, period=period, **m))
    pd.DataFrame(rows).to_csv(out / 'COMPARISON.csv', index=False)
    result = dict(passed=True, data_tag=audit['tag'], sha256=audit['sha256'],
                  full_search_repeated=False, cold_shortlist_candidates=len(shortlist),
                  fit_validation_metrics_match=True, complete_ranking_match=True,
                  frozen_best_id=reranked[0]['id'], reported_cases=len(reported),
                  job_rows_verified=sum(len(pd.read_csv(ref / 'trades' / (name + '.csv'))) for name in reported),
                  trade_fills=fillchecks, reported_metrics_and_mtm_match=True,
                  comparison_tolerance=1e-12, network_disabled=True,
                  elapsed_seconds=time.time()-start)
    write_json(out / 'verification.json', rt.clean(result))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
