"""Ex-post case labels for dataset statistics (never fed to strategies).

Writes / refreshes per-case ``labels`` on ``cases.json`` and a sidecar
``case_labels.json`` for easy aggregation. All fields are derived from the
same-day frozen 1m tapes after the close.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .dataset import Dataset, write_checksums
from .evaluate import (
    GAP_THRESHOLD, LABEL_BAND, SCENARIO_ZH, SPLIT_MINUTE, label_case,
)
from .registry import Registry
from .strategy import flatten_minute

SCHEMA = 'custody-case-labels/1'

# Intraday period boundaries (minutes from 09:30 regular open).
PERIODS = (
    ('open_drive', 0, 30),      # 09:30–10:00
    ('morning', 30, 90),        # 10:00–11:00
    ('midday', 90, 210),        # 11:00–13:00
    ('afternoon', 210, 330),    # 13:00–15:00
    ('power_hour', 330, 390),   # 15:00–16:00 (or session end)
)

RANGE_BUCKETS = (
    ('quiet', 0.0, 1.5),
    ('normal', 1.5, 3.0),
    ('wide', 3.0, 5.0),
    ('extreme', 5.0, 1e9),
)


def _move_tag(frac):
    if frac >= LABEL_BAND:
        return 'up'
    if frac <= -LABEL_BAND:
        return 'down'
    return 'flat'


def _range_bucket(day_range_pct):
    for name, lo, hi in RANGE_BUCKETS:
        if lo <= day_range_pct < hi:
            return name
    return 'extreme'


def _period_moves(bars):
    """Unsigned open→close move of each clock window as fraction of that window's range."""
    out = {}
    n = len(bars)
    for name, start, end in PERIODS:
        end = min(end, n)
        if start >= end or start >= n:
            out[name] = 'n/a'
            continue
        chunk = bars[start:end]
        o, c = chunk[0].open, chunk[-1].close
        hi = max(b.high for b in chunk)
        lo = min(b.low for b in chunk)
        span = hi - lo
        frac = (c - o) / span if span > 0 else 0.0
        out[name] = _move_tag(frac)
    return out


def _orb_break(bars, minutes=15):
    """Where session close sits vs the first ``minutes`` range."""
    if len(bars) < minutes + 1:
        return 'n/a'
    orb = bars[:minutes]
    hi, lo = max(b.high for b in orb), min(b.low for b in orb)
    close = bars[-1].close
    if close > hi:
        return 'break_up'
    if close < lo:
        return 'break_down'
    return 'inside'


def label_case_stats(data, flatten):
    """Build the full ex-post label dict for one CaseData."""
    path = label_case(data, flatten)
    und = data.underlying
    o, c = und[0].open, und[-1].close
    hi = max(b.high for b in und)
    lo = min(b.low for b in und)
    span = hi - lo
    oc_pct = 100.0 * (c - o) / o if o else 0.0
    day_range_pct = path['day_range_pct']

    # Gap vs prev_close (underlying, direction-agnostic).
    prev = data.case.prev_close
    if prev:
        gap = o / prev - 1
        gap_tag = 'up' if gap >= GAP_THRESHOLD else 'down' if gap <= -GAP_THRESHOLD else 'flat'
        gap_pct = round(100.0 * gap, 3)
    else:
        gap_tag, gap_pct = 'unknown', None

    underlying_day = 'up' if oc_pct >= 0.2 else 'down' if oc_pct <= -0.2 else 'flat'

    vols = [b.volume for b in und]
    session_volume = float(sum(vols))
    first_hour = und[: min(SPLIT_MINUTE, len(und))]
    first_hour_volume = float(sum(b.volume for b in first_hour))
    first_hour_share = (first_hour_volume / session_volume) if session_volume else 0.0
    median_bar = sorted(vols)[len(vols) // 2] if vols else 0.0
    rvol_first_hour = (first_hour_volume / (median_bar * len(first_hour))) if median_bar and first_hour else None

    opt = data.option
    option_volume = float(sum(b.volume for b in opt))

    labels = {
        'ex_post': True,
        'path_scenario': path['scenario'],
        'path_scenario_zh': SCENARIO_ZH[path['scenario']],
        'first_hour_frac': path['first_hour_move'],
        'rest_frac': path['rest_move'],
        'market_shape': path['market_shape'],
        'gap': gap_tag,
        'gap_pct': gap_pct,
        'underlying_day': underlying_day,
        'oc_pct': round(oc_pct, 3),
        'day_range_pct': day_range_pct,
        'range_bucket': _range_bucket(day_range_pct),
        'orb15': _orb_break(und, 15),
        'periods': _period_moves(und),
        'session_volume': round(session_volume, 1),
        'first_hour_volume': round(first_hour_volume, 1),
        'first_hour_volume_share': round(first_hour_share, 4),
        'rvol_first_hour_vs_median_bar': None if rvol_first_hour is None else round(rvol_first_hour, 3),
        'option_traded_bars': len(opt),
        'option_session_volume': round(option_volume, 1),
    }
    return labels


def build_labels(dataset_dir, strategy_id=None):
    root = Path(dataset_dir)
    ds = Dataset(root)
    reg = Registry()
    item = reg.get(strategy_id or reg.default_id)
    params = item['config']['params']
    rows = []
    for case in ds.cases:
        data = ds.load(case)
        flat = flatten_minute(params, data.session)
        labels = label_case_stats(data, flat)
        rows.append({
            'symbol': case.symbol,
            'contract': case.contract,
            'trade_date': case.trade_date,
            'direction': case.direction,
            'strike': case.strike,
            'labels': labels,
        })
    return rows


def apply_labels(dataset_dir, strategy_id=None):
    """Refresh cases.json labels + case_labels.json; rewrite checksums."""
    root = Path(dataset_dir)
    rows = build_labels(root, strategy_id=strategy_id)
    by_key = {(r['contract'], r['trade_date']): r['labels'] for r in rows}

    cases_path = root / 'cases.json'
    payload = json.loads(cases_path.read_text(encoding='utf-8'))
    for case in payload['cases']:
        key = (case['contract'], case['trade_date'])
        case['labels'] = by_key[key]
    cases_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    summary = {
        'schema': SCHEMA,
        'ex_post': True,
        'note': 'Statistics only. Never pass these labels into strategies or custody decisions.',
        'dataset': json.loads((root / 'manifest.json').read_text(encoding='utf-8')).get('dataset'),
        'case_count': len(rows),
        'path_scenario_counts': dict(Counter(r['labels']['path_scenario'] for r in rows)),
        'range_bucket_counts': dict(Counter(r['labels']['range_bucket'] for r in rows)),
        'gap_counts': dict(Counter(r['labels']['gap'] for r in rows)),
        'underlying_day_counts': dict(Counter(r['labels']['underlying_day'] for r in rows)),
        'orb15_counts': dict(Counter(r['labels']['orb15'] for r in rows)),
        'cases': rows,
    }
    (root / 'case_labels.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    manifest['case_labels_schema'] = SCHEMA
    manifest['case_labels'] = 'case_labels.json'
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    write_checksums(root)
    return {
        'cases': len(rows),
        'path_scenario_counts': summary['path_scenario_counts'],
        'range_bucket_counts': summary['range_bucket_counts'],
        'gap_counts': summary['gap_counts'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog='custody label', description=__doc__.splitlines()[0])
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--strategy', default=None, help='strategy id for flatten minute (default: registry default)')
    args = parser.parse_args(argv)
    summary = apply_labels(args.dataset, strategy_id=args.strategy)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0
