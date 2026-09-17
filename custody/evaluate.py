"""The evaluation standard for custody timing strategies (docs/STANDARD.md).

Offline, standard library only, deterministic. One command produces one report::

    python3 -m custody evaluate --dataset <extracted Release dir> --out <report dir>

What is fixed here (and therefore identical for every strategy version):

* the fill model: decision on a completed underlying bar at minute t; the order
  fills on the first traded option bar at minute >= t + delay, at that bar's close
  moved against us by ``slippage_fraction`` of the bar's high-low range, plus a
  per-contract fee on both sides;
* the must-trade platform rules: entry no later than the strategy's must-enter
  deadline, exit no later than its flatten time, no fill after the session close;
* ex-post scenario labels (never visible to strategies) and the mirror-symmetric
  scenario weighting that removes the direction mix of the dataset;
* the reference benchmark (buy right after the open, hold to flatten), the
  same-schedule shuffle null, the prefix-consistency check and the gates.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from .dataset import CaseData, Dataset
from .registry import Registry
from .strategy import Decision, build_strategy, deadlines, session_minute

SCENARIOS = ('trend_with', 'reversal_with', 'chop', 'reversal_against', 'trend_against')
SCENARIO_ZH = {'trend_with': '顺势单边', 'reversal_with': '先逆后顺', 'chop': '震荡',
               'reversal_against': '先顺后逆', 'trend_against': '逆势单边'}
MIRROR_PAIRS = (('trend_with', 'trend_against'), ('reversal_with', 'reversal_against'))
WITH_DIRECTION = ('trend_with', 'reversal_with')
SPLIT_MINUTE = 60            # first hour vs the rest of the day
LABEL_BAND = 0.25            # fraction of the day range that counts as a move
GAP_THRESHOLD = 0.002        # |open / prev_close - 1| that counts as a gap
BENCHMARK_ENTRY_MINUTE = 5   # reference: decide at 09:35, i.e. right after the open
MIN_PAYOFF_RATIO = 2.0
MIN_CASES_PER_SCENARIO = 5
MIN_OOS_SESSIONS = 20
NULL_DRAWS = 1000
BOOTSTRAP_DRAWS = 1000        # trading-day bootstrap for the direction-balanced metrics (report only)
NEIGHBOR_SCALE = 0.25          # parameter neighbours: each numeric parameter x0.75 and x1.25
CAPTURE_MIN_BEST = 0.20      # upside capture counts cases whose best reachable exit was >= +20%


@dataclass(frozen=True)
class FillModel:
    delay_minutes: int = 1
    slippage_fraction: float = 0.25
    fee_per_contract: float = 0.65
    multiplier: int = 100

    def __post_init__(self):
        if type(self.delay_minutes) is not int or self.delay_minutes < 1:
            raise ValueError('fill delay must be a whole number of minutes >= 1')
        if not 0 <= self.slippage_fraction <= 1 or self.fee_per_contract < 0:
            raise ValueError('invalid slippage/fee')

    def buy(self, bar):
        return min(bar.high, bar.close + self.slippage_fraction * (bar.high - bar.low))

    def sell(self, bar):
        return max(bar.low, bar.close - self.slippage_fraction * (bar.high - bar.low))


PRIMARY_FILL = FillModel()
STRESS_FILLS = {
    'slippage_0': FillModel(slippage_fraction=0.0),
    'slippage_0.5': FillModel(slippage_fraction=0.5),
    'delay_2m': FillModel(delay_minutes=2),
}


class OpenHoldBenchmark:
    """Reference only (not a strategy): enter right after the open, hold to flatten."""

    def __init__(self, params, direction, session):
        self.session = session
        self.must_enter, self.flatten = deadlines(params, session)
        self.phase = 'FLAT'

    def on_bar(self, bar):
        minute = session_minute(self.session, bar.close_time)
        if self.phase == 'FLAT' and minute >= BENCHMARK_ENTRY_MINUTE:
            self.phase = 'ENTERING'
        if self.phase == 'ENTERING':
            return Decision('ENTER', 'benchmark_open')
        if self.phase == 'IN' and minute >= self.flatten:
            return Decision('EXIT', 'scheduled_flatten')
        return Decision('HOLD' if self.phase == 'IN' else 'WAIT')

    def on_entry_filled(self, at, underlying_mark):
        self.phase = 'IN'


# ------------------------------------------------------------------ simulation
def simulate(data: CaseData, engine, params, fill=PRIMARY_FILL, until_minute=None):
    """Run one case through the must-trade platform rules. Returns a trade dict."""
    session, case = data.session, data.case
    must_enter, flatten = deadlines(params, session)
    options = {session_minute(session, b.close_time): b for b in data.option}
    last_minute = session_minute(session, data.underlying[-1].close_time)
    entry = exit_ = pending_entry = pending_exit = None
    decisions = []
    for bar in data.underlying:
        minute = session_minute(session, bar.close_time)
        if until_minute is not None and minute > until_minute:
            break
        option = options.get(minute)
        if pending_entry and entry is None and option and minute >= pending_entry['minute'] + fill.delay_minutes:
            if minute <= flatten:
                entry = dict(pending_entry, fill_minute=minute, price=fill.buy(option), bar_close=option.close)
                engine.on_entry_filled(option.close_time, bar.close)
        elif pending_exit and exit_ is None and option and minute >= pending_exit['minute'] + fill.delay_minutes:
            exit_ = dict(pending_exit, fill_minute=minute, price=fill.sell(option), bar_close=option.close)
            break
        decision = engine.on_bar(bar)
        decisions.append((minute, decision.action, decision.reason))
        if entry is None and pending_entry is None:
            if decision.action == 'ENTER':
                pending_entry = {'minute': minute, 'reason': decision.reason}
            elif minute >= must_enter:
                pending_entry = {'minute': minute, 'reason': 'platform_must_enter'}
        elif entry is not None and pending_exit is None:
            if decision.action == 'EXIT':
                pending_exit = {'minute': minute, 'reason': decision.reason}
            elif minute >= flatten:
                pending_exit = {'minute': minute, 'reason': 'platform_flatten'}
    trade = {'symbol': case.symbol, 'contract': case.contract, 'trade_date': case.trade_date,
             'direction': case.direction, 'entry': entry, 'exit': exit_, 'decisions': decisions,
             'failure': None, 'net_return': None, 'net_pnl': None, 'hold_minutes': None,
             'best_net_return': None}
    if until_minute is not None:
        return trade
    if entry is None:
        trade['failure'] = 'ENTRY_NOT_FILLED'
    elif exit_ is None:
        # The option never traded again after the exit decision: nothing could be sold, so
        # the 0DTE contract is settled at its intrinsic value at the close (usually zero).
        close = data.underlying[-1].close
        intrinsic = max(0.0, close - case.strike) if case.direction == 'LONG' else max(0.0, case.strike - close)
        exit_ = dict(pending_exit or {'minute': last_minute, 'reason': 'platform_flatten'},
                     fill_minute=last_minute, price=intrinsic, bar_close=None, settled_at_expiry=True)
        trade['exit'] = exit_
    if trade['failure'] is None and exit_ is not None:
        paid = entry['price'] * fill.multiplier
        sides = 1 if exit_.get('settled_at_expiry') else 2
        pnl = (exit_['price'] - entry['price']) * fill.multiplier - sides * fill.fee_per_contract
        trade.update(net_pnl=round(pnl, 4), net_return=pnl / paid,
                     hold_minutes=exit_['fill_minute'] - entry['fill_minute'])
        later = [fill.sell(b) for m, b in options.items()
                 if entry['fill_minute'] + fill.delay_minutes <= m <= last_minute]
        if later:
            best = (max(later) - entry['price']) * fill.multiplier - 2 * fill.fee_per_contract
            trade['best_net_return'] = best / paid
    return trade


def run_strategy(data: CaseData, item, fill=PRIMARY_FILL, until_minute=None):
    engine = build_strategy(item, data.case.direction, data.session, data.case.strike)
    return simulate(data, engine, item['config']['params'], fill, until_minute)


def run_benchmark(data: CaseData, item, fill=PRIMARY_FILL):
    engine = OpenHoldBenchmark(item['config']['params'], data.case.direction, data.session)
    return simulate(data, engine, item['config']['params'], fill)


# ------------------------------------------------------------------ labels
def label_case(data: CaseData, flatten_minute):
    """Ex-post day shape relative to the option's direction. Evaluation only."""
    sign = 1 if data.case.direction == 'LONG' else -1
    bars = data.underlying[:flatten_minute]
    open_, close = bars[0].open, bars[-1].close
    high, low = max(b.high for b in bars), min(b.low for b in bars)
    span = high - low
    split = bars[min(SPLIT_MINUTE, len(bars)) - 1].close
    first = sign * (split - open_) / span if span > 0 else 0.0
    rest = sign * (close - split) / span if span > 0 else 0.0

    def grade(x):
        return 1 if x >= LABEL_BAND else -1 if x <= -LABEL_BAND else 0

    shape = (grade(first), grade(rest))
    if shape in ((1, 1), (0, 1), (1, 0)):
        scenario = 'trend_with'
    elif shape in ((-1, -1), (0, -1), (-1, 0)):
        scenario = 'trend_against'
    elif shape == (-1, 1):
        scenario = 'reversal_with'
    elif shape == (1, -1):
        scenario = 'reversal_against'
    else:
        scenario = 'chop'
    market = 'unknown_prev_close'
    prev = data.case.prev_close
    if prev:
        gap = open_ / prev - 1
        gap_name = '高开' if gap >= GAP_THRESHOLD else '低开' if gap <= -GAP_THRESHOLD else '平开'
        net = (close - open_) / span if span > 0 else 0.0
        market = gap_name + ('高走' if net >= LABEL_BAND else '低走' if net <= -LABEL_BAND else '震荡')
    return {'scenario': scenario, 'first_hour_move': round(first, 3), 'rest_move': round(rest, 3),
            'day_range_pct': round(100 * span / open_, 3), 'market_shape': market}


def mirror_weights(scenarios, hit_rate=0.5):
    """Per-case weights that give each mirror pair its pooled frequency, split by hit_rate.

    A dataset whose upstream direction was right 90% of the time and one where it was
    wrong 90% of the time get the same headline numbers. Pairs with an empty side are
    excluded (weight 0) and reported as a coverage gap.
    """
    counts = Counter(scenarios)
    total = len(scenarios)
    per_scenario, gaps = {}, []
    for with_, against in MIRROR_PAIRS:
        pooled = (counts[with_] + counts[against]) / total if total else 0
        if counts[with_] and counts[against]:
            per_scenario[with_] = pooled * hit_rate / counts[with_]
            per_scenario[against] = pooled * (1 - hit_rate) / counts[against]
        else:
            per_scenario[with_] = per_scenario[against] = 0.0
            if counts[with_] or counts[against]:
                gaps.append('%s/%s' % (with_, against))
    per_scenario['chop'] = 1 / total if counts['chop'] else 0.0
    weights = [per_scenario[s] for s in scenarios]
    norm = sum(weights)
    return ([w / norm for w in weights] if norm else weights), gaps


# ------------------------------------------------------------------ metrics
def weighted_metrics(values, weights=None):
    pairs = [(v, 1.0 if weights is None else w) for v, w in zip(values, weights or [None] * len(values))
             if v is not None and (weights is None or w > 0)]
    if not pairs:
        return {'n': 0, 'expectancy': None, 'win_rate': None, 'average_win': None, 'average_loss': None,
                'payoff_ratio': None, 'profit_factor': None}
    total = sum(w for _, w in pairs)
    wins = [(v, w) for v, w in pairs if v > 0]
    losses = [(v, w) for v, w in pairs if v < 0]
    avg_win = sum(v * w for v, w in wins) / sum(w for _, w in wins) if wins else None
    avg_loss = -sum(v * w for v, w in losses) / sum(w for _, w in losses) if losses else None
    return {
        'n': len(pairs),
        'expectancy': sum(v * w for v, w in pairs) / total,
        'win_rate': sum(w for _, w in wins) / total,
        'average_win': avg_win,
        'average_loss': avg_loss,
        'payoff_ratio': avg_win / avg_loss if avg_win is not None and avg_loss else None,
        'profit_factor': (sum(v * w for v, w in wins) / -sum(v * w for v, w in losses)) if losses else None,
    }


def summarize(trades, labels, hit_rate=0.5):
    scenarios = [l['scenario'] for l in labels]
    weights, gaps = mirror_weights(scenarios, hit_rate)
    returns = [t['net_return'] for t in trades]
    dollars = [t['net_pnl'] for t in trades]
    by_scenario = {}
    for name in SCENARIOS:
        idx = [i for i, s in enumerate(scenarios) if s == name]
        sub = [trades[i] for i in idx]
        captures = [min(max(t['net_return'] / t['best_net_return'], 0.0), 1.0) for t in sub
                    if t['net_return'] is not None and (t['best_net_return'] or 0) >= CAPTURE_MIN_BEST]
        by_scenario[name] = dict(weighted_metrics([returns[i] for i in idx]), label_zh=SCENARIO_ZH[name],
                                 median=statistics.median([returns[i] for i in idx if returns[i] is not None])
                                 if any(returns[i] is not None for i in idx) else None,
                                 upside_capture=statistics.mean(captures) if captures else None)
    completed = sum(t['failure'] is None for t in trades)
    return {
        'cases': len(trades),
        'completed': completed,
        'completion_rate': completed / len(trades) if trades else 0.0,
        'failures': Counter(t['failure'] for t in trades if t['failure']),
        'balanced': weighted_metrics(returns, weights),
        'balanced_dollars': weighted_metrics(dollars, weights),
        'raw': weighted_metrics(returns),
        'dollars_total': sum(d for d in dollars if d is not None) if completed else None,
        'coverage_gaps': gaps,
        'by_scenario': by_scenario,
        'with_direction': weighted_metrics([r for r, l in zip(returns, labels) if l['scenario'] in WITH_DIRECTION]),
        'hold_minutes_median': statistics.median([t['hold_minutes'] for t in trades if t['hold_minutes'] is not None])
        if completed else None,
        'entry_reasons': Counter(t['entry']['reason'] for t in trades if t['entry']),
        'exit_reasons': Counter(t['exit']['reason'] for t in trades if t['exit']),
    }


def shuffle_null(datas, trades, labels, fill=PRIMARY_FILL, draws=NULL_DRAWS, seed=20260916):
    """Same-schedule null: reassign the strategy's own (entry, exit) minutes across cases.

    Keeps the distribution of entry times and holding periods, destroys the link
    between a specific day's tape and the chosen minutes. p = share of draws whose
    balanced expectancy (payoff ratio) is at least the strategy's.
    """
    schedule = [(t['entry']['minute'], t['exit']['minute']) for t in trades if t['failure'] is None]
    if len(schedule) < 2:
        return None
    weights, _ = mirror_weights([l['scenario'] for l in labels])
    observed = weighted_metrics([t['net_return'] for t in trades], weights)
    if observed['expectancy'] is None:
        return None  # direction-balanced metrics undefined for this dataset
    tapes = []
    for data in datas:
        options = sorted((session_minute(data.session, b.close_time), b) for b in data.option)
        tapes.append(options)

    def replay(options, entry_minute, exit_minute):
        buy = next(((m, b) for m, b in options if m >= entry_minute + fill.delay_minutes), None)
        if buy is None:
            return None
        sell = next(((m, b) for m, b in options if m >= max(exit_minute, buy[0]) + fill.delay_minutes), None)
        if sell is None:
            return None
        paid = fill.buy(buy[1]) * fill.multiplier
        return ((fill.sell(sell[1]) - fill.buy(buy[1])) * fill.multiplier - 2 * fill.fee_per_contract) / paid

    rng = random.Random(seed)
    ge_expectancy = ge_payoff = valid = 0
    for _ in range(draws):
        rng.shuffle(schedule)
        values = [replay(tapes[i], *schedule[i % len(schedule)]) for i in range(len(datas))]
        m = weighted_metrics(values, weights)
        if m['expectancy'] is None:
            continue
        valid += 1
        ge_expectancy += m['expectancy'] >= observed['expectancy']
        if m['payoff_ratio'] is not None and observed['payoff_ratio'] is not None:
            ge_payoff += m['payoff_ratio'] >= observed['payoff_ratio']
    return {'draws': valid, 'p_expectancy': (ge_expectancy + 1) / (valid + 1),
            'p_payoff_ratio': (ge_payoff + 1) / (valid + 1)}


def day_bootstrap(datas, labels, trades, bench, draws=BOOTSTRAP_DRAWS, seed=20260917):
    """95% intervals of the balanced metrics when whole trading days are resampled."""
    days = sorted({d.case.trade_date for d in datas})
    by_day = {day: [i for i, d in enumerate(datas) if d.case.trade_date == day] for day in days}
    rng = random.Random(seed)
    samples = {'strategy_expectancy': [], 'strategy_payoff_ratio': [], 'benchmark_expectancy': [],
               'expectancy_minus_benchmark': []}
    for _ in range(draws):
        idx = [i for _ in days for i in by_day[rng.choice(days)]]
        sub_labels = [labels[i] for i in idx]
        s_bal = summarize([trades[i] for i in idx], sub_labels)['balanced']
        b_bal = summarize([bench[i] for i in idx], sub_labels)['balanced']
        if s_bal['expectancy'] is None or b_bal['expectancy'] is None:
            continue
        samples['strategy_expectancy'].append(s_bal['expectancy'])
        samples['benchmark_expectancy'].append(b_bal['expectancy'])
        samples['expectancy_minus_benchmark'].append(s_bal['expectancy'] - b_bal['expectancy'])
        if s_bal['payoff_ratio'] is not None:
            samples['strategy_payoff_ratio'].append(s_bal['payoff_ratio'])

    def interval(values):
        values = sorted(values)
        return [values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]] if values else None
    return {'draws': draws, **{k: interval(v) for k, v in samples.items()}}


def prefix_consistency(datas, trades, item, fill=PRIMARY_FILL):
    """Re-run each case truncated at its entry/exit decision minute; decisions must match."""
    mismatches = []
    for data, trade in zip(datas, trades):
        for leg in ('entry', 'exit'):
            if not trade[leg] or (trade[leg]['reason'] or '').startswith('platform_'):
                continue
            minute = trade[leg]['minute']
            full = next(d for d in trade['decisions'] if d[0] == minute)
            cut = run_strategy(data, item, fill, until_minute=minute)['decisions']
            if not cut or cut[-1] != full:
                mismatches.append({'contract': trade['contract'], 'leg': leg, 'minute': minute})
    return {'passed': not mismatches, 'mismatches': mismatches}


# ------------------------------------------------------------------ verdict
def gates(summary, benchmark, robust):
    s, b = summary, benchmark
    against, bench_against = s['by_scenario']['trend_against'], b['by_scenario']['trend_against']
    checks = {
        'G1_completion_100pct': s['completion_rate'] == 1.0,
        'G3_expectancy_beats_benchmark': _all([_gt(s['balanced']['expectancy'], b['balanced']['expectancy']),
                                               _gt(s['balanced_dollars']['expectancy'], b['balanced_dollars']['expectancy'])]),
        'G4_payoff_ratio_at_least_%g' % MIN_PAYOFF_RATIO: _ge(s['balanced']['payoff_ratio'], MIN_PAYOFF_RATIO),
        'G5_trend_against_loss_smaller_than_benchmark': _gt(against['expectancy'], bench_against['expectancy']),
        'G6_with_direction_expectancy_positive': _gt(s['with_direction']['expectancy'], 0.0),
        'G7_payoff_ratio_beats_benchmark': _all([_gt(s['balanced']['payoff_ratio'], b['balanced']['payoff_ratio']),
                                                 _gt(s['balanced_dollars']['payoff_ratio'], b['balanced_dollars']['payoff_ratio'])]),
        'G8_both_halves_beat_benchmark': robust['halves']['passed'],
        'G9_leave_one_day_out_stable': robust['leave_one_day_out']['passed'],
        'G10_parameter_neighbors_beat_benchmark': robust['neighbors']['passed'],
        'G11_profit_factor_above_1': _gt(s['balanced']['profit_factor'], 1.0),
    }
    return checks


def _beats(strategy_balanced, benchmark_balanced):
    return _all([_gt(strategy_balanced['expectancy'], benchmark_balanced['expectancy']),
                 _gt(strategy_balanced['payoff_ratio'], benchmark_balanced['payoff_ratio'])])


def neighbor_params(params, engine_validate, scale=NEIGHBOR_SCALE):
    """Each numeric parameter moved down and up by ``scale`` (integers by at least 1)."""
    out = []
    for name, value in sorted(params.items()):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value == 0:
            continue
        for sign in (-1, 1):
            moved = value * (1 + sign * scale)
            if isinstance(value, int):
                moved = int(round(moved))
                if moved == value:
                    moved = value + sign
            else:
                moved = round(moved, 6)
            candidate = dict(params, **{name: moved})
            try:
                engine_validate(candidate)
            except ValueError:
                continue
            out.append((name, value, moved, candidate))
    return out


def robustness(datas, labels, trades, bench, item, fill=PRIMARY_FILL):
    """Anti-overfitting checks: time halves, leave-one-day-out, parameter neighbours."""
    from .engines import ENGINES
    dates = sorted({d.case.trade_date for d in datas})
    halves = {}
    for name, days in (('first_half', dates[:len(dates) // 2]), ('second_half', dates[len(dates) // 2:])):
        pairs = [(t['net_return'], b['net_return']) for d, t, b in zip(datas, trades, bench)
                 if d.case.trade_date in days and b['net_return'] is not None]
        strategy = _mean(x for x, _ in pairs) if pairs and all(x is not None for x, _ in pairs) else None
        halves[name] = {'days': [days[0], days[-1]] if days else None, 'cases': len(pairs),
                        'strategy_mean': strategy, 'benchmark_mean': _mean(y for _, y in pairs)}
    halves['passed'] = (None if len(dates) < 2 else
                        _all(_gt(h['strategy_mean'], h['benchmark_mean']) for h in halves.values() if isinstance(h, dict)))
    lodo = []
    for day in dates:
        keep = [i for i, d in enumerate(datas) if d.case.trade_date != day]
        sub_labels = [labels[i] for i in keep]
        s_bal = summarize([trades[i] for i in keep], sub_labels)['balanced']
        b_bal = summarize([bench[i] for i in keep], sub_labels)['balanced']
        lodo.append({'dropped': day, 'passed': _beats(s_bal, b_bal),
                     'strategy_expectancy': s_bal['expectancy'], 'strategy_payoff_ratio': s_bal['payoff_ratio'],
                     'benchmark_expectancy': b_bal['expectancy'], 'benchmark_payoff_ratio': b_bal['payoff_ratio']})
    bench_balanced = summarize(bench, labels)['balanced']
    neighbors = []
    engine = ENGINES[item['config']['engine']]
    for name, value, moved, params in neighbor_params(item['config']['params'], engine.validate):
        variant = json.loads(json.dumps(item))
        variant['config']['params'] = params
        try:
            bal = summarize([run_strategy(d, variant, fill) for d in datas], labels)['balanced']
        except ValueError:
            continue  # e.g. deadlines that do not fit the session
        neighbors.append({'param': name, 'from': value, 'to': moved, 'passed': _beats(bal, bench_balanced),
                          'expectancy': bal['expectancy'], 'payoff_ratio': bal['payoff_ratio']})
    return {
        'halves': halves,
        'leave_one_day_out': {'passed': None if len(dates) < 2 else _all(r['passed'] for r in lodo),
                              'failed_days': [r['dropped'] for r in lodo if r['passed'] is False],
                              'runs': lodo},
        'neighbors': {'passed': _all(r['passed'] for r in neighbors), 'count': len(neighbors),
                      'failed': [r for r in neighbors if r['passed'] is False], 'runs': neighbors},
    }


def _gt(a, b):
    """True / False, or None when a side is undefined (the data cannot judge this gate)."""
    return None if a is None or b is None else a > b


def _ge(a, b):
    return None if a is None or b is None else a >= b


def _all(values):
    """Tri-state AND: False if anything failed, None if something could not be judged."""
    values = list(values)
    if any(v is False for v in values):
        return False
    if not values or any(v is None for v in values):
        return None
    return True


def verdict(checks, prefix_ok, coverage_ok, oos_sessions, both_sides_ok):
    if checks['G1_completion_100pct'] is not True or not prefix_ok:
        return 'INVALID'
    if any(v is False for v in checks.values()):
        return 'REJECT'
    if (any(v is None for v in checks.values()) or not coverage_ok or not both_sides_ok
            or oos_sessions < MIN_OOS_SESSIONS):
        return 'PROVISIONAL'
    return 'ACCEPT'


# ------------------------------------------------------------------ report
def evaluate(dataset_dir, strategy_id=None, registry=None, null_draws=NULL_DRAWS):
    registry = registry or Registry()
    item = registry.get(strategy_id or registry.default_id)
    params = item['config']['params']
    dataset = Dataset(dataset_dir)
    datas = [dataset.load(case) for case in dataset.cases]
    labels = [label_case(d, deadlines(params, d.session)[1]) for d in datas]
    trades = [run_strategy(d, item) for d in datas]
    bench = [run_benchmark(d, item) for d in datas]
    developed = item['config'].get('developed_on', {})
    cutoff = developed.get('sessions_through')
    oos_idx = [i for i, d in enumerate(datas) if cutoff and d.case.trade_date > cutoff]
    report = {
        'standard': 'docs/STANDARD.md',
        'strategy': {k: item[k] for k in ('strategy_id', 'sha256', 'engine', 'status')},
        'strategy_params': params,
        'developed_on': developed,
        'dataset': {'name': dataset.name, 'checksums_sha256': dataset.fingerprint, 'checksums_verified': dataset.checksums_verified,
                    'cases': len(datas), 'sessions': len(dataset.sessions()),
                    'window': [dataset.sessions()[0], dataset.sessions()[-1]] if datas else None,
                    'selections': Counter(d.case.selection or 'unspecified' for d in datas),
                    'sides': dataset.sides_report()},
        'fill_model': asdict(PRIMARY_FILL),
        'summary': summarize(trades, labels),
        'benchmark_open_hold': summarize(bench, labels),
        'direction_skill_0.6': {'strategy': summarize(trades, labels, 0.6)['balanced'],
                                'benchmark': summarize(bench, labels, 0.6)['balanced']},
        'stress': {},
        'shuffle_null': shuffle_null(datas, trades, labels, draws=null_draws) if null_draws else None,
        'prefix_consistency': prefix_consistency(datas, trades, item),
        'bootstrap_by_day': day_bootstrap(datas, labels, trades, bench, draws=BOOTSTRAP_DRAWS if null_draws else 50),
        'market_shapes': _market_shapes(trades, bench, labels),
        'cases': [dict(t, decisions=None, benchmark_net_return=b['net_return'], **l)
                  for t, b, l in zip(trades, bench, labels)],
    }
    for name, fill in STRESS_FILLS.items():
        stressed = [run_strategy(d, item, fill) for d in datas]
        stressed_bench = [run_benchmark(d, item, fill) for d in datas]
        report['stress'][name] = {'fill_model': asdict(fill), 'strategy': summarize(stressed, labels)['balanced'],
                                  'benchmark': summarize(stressed_bench, labels)['balanced']}
    coverage = {s: report['summary']['by_scenario'][s]['n'] for s in SCENARIOS}
    coverage_ok = all(n >= MIN_CASES_PER_SCENARIO for n in coverage.values())
    report['robustness'] = robustness(datas, labels, trades, bench, item)
    in_sample_checks = gates(report['summary'], report['benchmark_open_hold'], report['robustness'])
    oos = None
    if oos_idx:
        pick = lambda seq: [seq[i] for i in oos_idx]
        oos_summary = summarize(pick(trades), pick(labels))
        oos_bench = summarize(pick(bench), pick(labels))
        oos_robust = robustness(pick(datas), pick(labels), pick(trades), pick(bench), item)
        oos = {'sessions': len({datas[i].case.trade_date for i in oos_idx}), 'summary': oos_summary,
               'benchmark_open_hold': oos_bench, 'robustness': oos_robust,
               'gates': gates(oos_summary, oos_bench, oos_robust)}
    oos_sessions = oos['sessions'] if oos else 0
    decisive = oos['gates'] if oos and oos_sessions >= MIN_OOS_SESSIONS else in_sample_checks
    report['coverage'] = {'cases_per_scenario': coverage, 'minimum': MIN_CASES_PER_SCENARIO, 'ok': coverage_ok}
    report['gates_in_sample'] = in_sample_checks
    report['out_of_sample'] = oos
    report['verdict'] = verdict(decisive, report['prefix_consistency']['passed'], coverage_ok, oos_sessions,
                                report['dataset']['sides']['ok'])
    return _plain(report)


def _market_shapes(trades, bench, labels):
    groups = defaultdict(list)
    for t, b, l in zip(trades, bench, labels):
        groups[(l['market_shape'], t['direction'])].append((t['net_return'], b['net_return']))
    return {'%s|%s' % key: {'n': len(v), 'strategy_mean': _mean(x for x, _ in v), 'benchmark_mean': _mean(y for _, y in v)}
            for key, v in sorted(groups.items())}


def _mean(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def _plain(obj):
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, 6)
    return obj


def _pct(x):
    return '—' if x is None else '%+.1f%%' % (100 * x)


def _money(x):
    return '—' if x is None else '%+.0f 美元' % x


def _share(x):
    return '—' if x is None else '%.1f%%' % (100 * x)


def _num(x, fmt='%.2f'):
    return '—' if x is None else fmt % x


GATE_TEXT = {
    'G1_completion_100pct': 'G1 每张合约都完成了一买一卖',
    'G3_expectancy_beats_benchmark': 'G3 平均每笔收益好于对照组（按收益率和按美元都要好）',
    'G4_payoff_ratio_at_least_%g' % MIN_PAYOFF_RATIO: 'G4 盈亏比 ≥ %g' % MIN_PAYOFF_RATIO,
    'G5_trend_against_loss_smaller_than_benchmark': 'G5 方向错（逆势单边）时亏得比对照组少',
    'G6_with_direction_expectancy_positive': 'G6 方向对（顺势单边 + 先逆后顺）时平均赚钱',
    'G7_payoff_ratio_beats_benchmark': 'G7 盈亏比高于对照组（按收益率和按美元都要高）',
    'G8_both_halves_beat_benchmark': 'G8 前一半交易日、后一半交易日各自都赢对照组（防过拟合）',
    'G9_leave_one_day_out_stable': 'G9 去掉任意一天，平均收益和盈亏比仍都赢对照组（防过拟合）',
    'G10_parameter_neighbors_beat_benchmark': 'G10 每个参数上下浮动 25%，平均收益和盈亏比仍都赢对照组（防过拟合）',
    'G11_profit_factor_above_1': 'G11 整体赚钱（总赚 ÷ 总亏 > 1）',
}
VERDICT_TEXT = {
    'ACCEPT': '达标，可以把策略状态改为 accepted',
    'PROVISIONAL': '能判断的门槛都过了，但数据还不够（有门槛无法判断、场景样本太少或样本外交易日不足），暂不能上线',
    'REJECT': '没达到标准，不能上线',
    'INVALID': '实现有问题（没完成交易或偷看了未来数据），结果无效',
}


def _hhmm(minute):
    return '—' if minute is None else '%02d:%02d' % divmod(570 + minute, 60)


def render_markdown(report):
    s, b = report['summary'], report['benchmark_open_hold']
    counts = report['coverage']['cases_per_scenario']
    wrong = counts['trend_against'] + counts['reversal_against']
    right = counts['trend_with'] + counts['reversal_with']
    example = next((c for c in report['cases'] if c['net_return'] is not None and c['benchmark_net_return'] is not None), None)
    fill = report['fill_model']
    lines = [
        '# 评测报告：%s' % report['strategy']['strategy_id'],
        '',
        '**结论：%s** —— %s。' % (report['verdict'], VERDICT_TEXT[report['verdict']]),
        '',
        '## 先看这里：这份报告在比什么',
        '',
        '1. **怎么算一笔**：对数据集里的每一张合约，用当天真实的 1 分钟行情从开盘逐分钟回放。策略说「买」之后，'
        '用下一根有成交的期权 K 线的价格买入（再往不利方向多付该 K 线振幅的 %.0f%%，并扣每张 $%.2f 手续费），卖出同理。'
        '收益 = 这一笔赚的钱 ÷ 买入时付的权利金。' % (100 * fill['slippage_fraction'], fill['fee_per_contract']),
        '2. **对照组**：同一张合约，用「不择时」的笨办法再算一遍——09:35 直接买入，拿到 15:45 卖出。'
        '择时策略存在的意义就是要比这个笨办法好；比不过，就说明择时没有用。',
        '3. **为什么 CALL 和 PUT 要一起验证、要「方向对错各半」来算平均**：每个标的每天应当同时评测同一行权价的 CALL 和 PUT，'
        '这样当天无论涨跌，方向对和方向错的合约各占一半，结果和上游选方向的对错无关。%s'
        '这份数据集里方向错的合约 %d 张、方向对的 %d 张（其余是震荡）。如果直接平均，哪个策略砍仓快哪个就显得好，比的其实是数据集而不是择时。'
        '所以把「方向对的日子」和「方向错的日子」各按一半权重平均，相当于假设上游选方向一半对一半错。' % (
            '本数据集两边齐全。' if report['dataset']['sides']['ok'] else '**本数据集两边不全，结论最高只能 PROVISIONAL。**', wrong, right),
    ]
    if example:
        lines.append('4. **举例**：%s %s %s —— 策略 %s 买、%s 卖（%s），收益 %s；对照组收益 %s。' % (
            example['symbol'], example['trade_date'], 'CALL' if example['direction'] == 'LONG' else 'PUT',
            _hhmm(example['entry']['fill_minute']), _hhmm(example['exit']['fill_minute']),
            REASON_ZH.get(example['exit']['reason'], example['exit']['reason']),
            _pct(example['net_return']), _pct(example['benchmark_net_return'])))
    lines += [
        '',
        '数据集：`%s`，%d 张合约 / %d 个交易日（%s → %s）；策略参数截止 `%s` 的数据设计，之后的交易日才算样本外。' % (
            report['dataset']['name'], report['dataset']['cases'], report['dataset']['sessions'],
            *(report['dataset']['window'] or ['—', '—']), report['developed_on'].get('sessions_through', '—')),
        '',
        '## 1. 过没过门槛',
        '',
        '| 门槛 | 结果 |', '|---|---|',
    ]
    gate_rows = [(GATE_TEXT.get(k, k), v) for k, v in report['gates_in_sample'].items()]
    gate_rows.insert(1, ('G2 没有偷看未来数据（截断到决策那一分钟重放，决策不变）', report['prefix_consistency']['passed']))
    gate_text = {True: '通过', False: '**未通过**', None: '不适用（这份数据无法判断）'}
    lines += ['| %s | %s |' % (name, gate_text[ok]) for name, ok in gate_rows]
    lines += [
        '| 每个标的每天 CALL 和 PUT 两边都有（同一行权价） | %s |' % (
            '满足（%d 组）' % report['dataset']['sides']['both_sides'] if report['dataset']['sides']['ok'] else
            '**不满足**：%d 组里只有 %d 组两边齐全' % (report['dataset']['sides']['symbol_sessions'], report['dataset']['sides']['both_sides'])),
        '| 每种走势至少 %d 张合约 | %s |' % (MIN_CASES_PER_SCENARIO, '满足' if report['coverage']['ok'] else '**不足**'),
        '| 样本外交易日至少 %d 个 | %d 个 |' % (MIN_OOS_SESSIONS, (report['out_of_sample'] or {}).get('sessions', 0)),
        '',
        '## 2. 总体（方向对错各半）',
        '',
        '| 指标 | 说明 | 策略 | 对照组（09:35 买，拿到 15:45） |', '|---|---|---:|---:|',
    ]
    rows = (('expectancy', '平均每笔收益', '所有笔收益的平均', _pct),
            ('payoff_ratio', '盈亏比', '平均赚的一笔 ÷ 平均亏的一笔', _num),
            ('win_rate', '胜率', '赚钱的笔数占比', _share),
            ('average_win', '平均赚', '赚钱那些笔的平均收益', _share),
            ('average_loss', '平均亏', '亏钱那些笔的平均亏损', _share),
            ('profit_factor', '总赚 ÷ 总亏', '大于 1 才是整体赚钱', _num))
    for key, name, meaning, fmt in rows:
        lines.append('| %s | %s | %s | %s |' % (name, meaning, fmt(s['balanced'][key]), fmt(b['balanced'][key])))
    lines += [
        '| 中位持仓 | 分钟 | %s | %s |' % (_num(s['hold_minutes_median'], '%.0f'), _num(b['hold_minutes_median'], '%.0f')),
        '',
        '### 同一批合约直接对比（不加权）',
        '',
        '策略和对照组交易的是完全相同的合约，方向构成一样，所以这张表不需要加权也是公平的比较；'
        '但它的绝对数值会受这份数据里方向对错比例影响。%s' % (
            '**这份数据方向对错只有一边，上面「各半」指标算不出来，以这张表为准。**' if s['balanced']['expectancy'] is None else ''),
        '',
        '| 指标 | 策略 | 对照组 |', '|---|---:|---:|',
    ]
    for key, name, _meaning, fmt in rows:
        lines.append('| %s | %s | %s |' % (name, fmt(s['raw'][key]), fmt(b['raw'][key])))
    lines += [
        '| 美元合计（每张合约） | %s | %s |' % (_money(s['dollars_total']), _money(b['dollars_total'])),
        '',
        '## 3. 分走势看（按当天实际走势事后分类，策略运行时看不到）',
        '',
        '走势是相对这张合约的方向说的：对 CALL，「顺势单边」= 当天一路涨；对 PUT，「顺势单边」= 当天一路跌。',
        '',
        '| 当天走势 | 合约数 | 策略平均收益 | 对照组平均收益 | 策略盈亏比 | 拿到了多少涨幅* |', '|---|---:|---:|---:|---:|---:|',
    ]
    for name in SCENARIOS:
        x, y = s['by_scenario'][name], b['by_scenario'][name]
        lines.append('| %s | %d | %s | %s | %s | %s |' % (
            SCENARIO_ZH[name], x['n'], _pct(x['expectancy']), _pct(y['expectancy']), _num(x['payoff_ratio']),
            _share(x['upside_capture'])))
    lines += ['', '\\* 只统计入场后「最好能卖到 +%d%% 以上」的合约：实际收益占最好可能收益的比例（亏钱记 0%%）。' % round(100 * CAPTURE_MIN_BEST)]
    null = report['shuffle_null']
    lines += [
        '',
        '## 4. 结果靠不靠得住',
        '',
        ('- **随机时点对照**：把策略的买卖时间随机换到别的合约上，重复 %s 次。随机时间的平均收益不比策略差的比例 p = %s，盈亏比 p = %s。'
         'p 越小越说明择时真的用上了当天走势；p 大于 0.1 基本等于没有择时能力。' % (
             null['draws'], _num(null['p_expectancy'], '%.2f'), _num(null['p_payoff_ratio'], '%.2f'))
         if null else '- **随机时点对照**：无法计算（这份数据算不出方向对错各半的指标）。'),
        ('- **按交易日重抽样 %d 次的 95%% 区间**：策略平均每笔 %s ~ %s，盈亏比 %s ~ %s；策略减对照组的平均收益 %s ~ %s（区间跨过 0 说明还不能确定比对照组好）。' % (
            report['bootstrap_by_day']['draws'], *[_pct(x) for x in report['bootstrap_by_day']['strategy_expectancy']],
            *[_num(x) for x in (report['bootstrap_by_day']['strategy_payoff_ratio'] or [None, None])],
            *[_pct(x) for x in report['bootstrap_by_day']['expectancy_minus_benchmark']])
         if report['bootstrap_by_day']['strategy_expectancy'] else '- **按交易日重抽样**：无法计算（交易日太少或方向对错只有一边）。'),
        '- **假设上游方向对 60%%**：策略平均每笔 %s、盈亏比 %s；对照组平均每笔 %s。' % (
            _pct(report['direction_skill_0.6']['strategy']['expectancy']),
            _num(report['direction_skill_0.6']['strategy']['payoff_ratio']),
            _pct(report['direction_skill_0.6']['benchmark']['expectancy'])),
    ]
    robust = report['robustness']
    halves = robust['halves']
    lines.append('- **前后两半**：前一半（%s）策略平均 %s / 对照组 %s；后一半（%s）策略平均 %s / 对照组 %s。' % (
        ' → '.join(halves['first_half']['days'] or ['—']), _pct(halves['first_half']['strategy_mean']),
        _pct(halves['first_half']['benchmark_mean']), ' → '.join(halves['second_half']['days'] or ['—']),
        _pct(halves['second_half']['strategy_mean']), _pct(halves['second_half']['benchmark_mean'])))
    lodo = robust['leave_one_day_out']
    if lodo['passed'] is None and not lodo['failed_days']:
        lodo_text = '无法判断（交易日太少，或去掉一天后方向对错两边样本不全）'
    elif not lodo['failed_days']:
        lodo_text = '全部仍赢对照组'
    else:
        lodo_text = '去掉这些天后输给对照组：' + '、'.join(lodo['failed_days'])
    lines.append('- **去掉任意一天**：共 %d 次，%s。' % (len(lodo['runs']), lodo_text))
    failed = robust['neighbors']['failed']
    if robust['neighbors']['passed'] is None and not failed:
        neighbor_text = '无法判断（这份数据算不出方向对错各半的指标）'
    elif not failed:
        neighbor_text = '全部仍赢对照组'
    else:
        neighbor_text = '输给对照组的：' + '；'.join(
            '%s %s→%s（平均 %s，盈亏比 %s）' % (r['param'], r['from'], r['to'], _pct(r['expectancy']), _num(r['payoff_ratio']))
            for r in failed)
    lines.append('- **参数上下浮动 25%%**：共 %d 组，%s。' % (robust['neighbors']['count'], neighbor_text))
    stress_names = {'slippage_0': '不算滑点', 'slippage_0.5': '滑点加倍（让出振幅 50%）', 'delay_2m': '成交再晚 1 分钟'}
    for name, row in report['stress'].items():
        lines.append('- **%s**：策略平均每笔 %s、盈亏比 %s；对照组平均每笔 %s。' % (
            stress_names.get(name, name), _pct(row['strategy']['expectancy']), _num(row['strategy']['payoff_ratio']),
            _pct(row['benchmark']['expectancy'])))
    lines += ['', '## 5. 逐笔明细', '',
              '| 标的 | 日期 | 合约 | 当天走势 | 买入 | 买入原因 | 卖出 | 卖出原因 | 策略收益 | 对照组收益 |',
              '|---|---|---|---|---|---|---|---|---:|---:|']
    for c in report['cases']:
        lines.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
            c['symbol'], c['trade_date'], 'CALL' if c['direction'] == 'LONG' else 'PUT', SCENARIO_ZH[c['scenario']],
            _hhmm(c['entry'] and c['entry']['fill_minute']), REASON_ZH.get(c['entry'] and c['entry']['reason'], c['entry'] and c['entry']['reason']),
            _hhmm(c['exit'] and c['exit']['fill_minute']), REASON_ZH.get(c['exit'] and c['exit']['reason'], c['exit'] and c['exit']['reason']),
            _pct(c['net_return']), _pct(c['benchmark_net_return'])))
    return '\n'.join(lines) + '\n'


REASON_ZH = {
    'trend_breakout': '顺势突破', 'reversal_reclaim': '反转收复', 'late_confirmation': '午后放宽确认',
    'must_trade_deadline': '到点必须买', 'platform_must_enter': '平台强制买', 'friction_deadline': '虚值过深提前强制买',
    'invalidation_stop': '止损', 'breakeven_stop': '正股跌回买入价离场', 'trailing_stop': '从高点回撤离场',
    'no_progress': '迟迟不涨离场', 'scheduled_flatten': '收盘前强平', 'platform_flatten': '平台强平',
    'charm_exit': '午后仍虚值离场', 'giveback_stop': '回吐过半离场',
}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='custody evaluate', description=__doc__.splitlines()[0])
    parser.add_argument('--dataset', required=True, help='extracted dataset directory (e.g. Release custody-train-0dte)')
    parser.add_argument('--strategy', default=None, help='registered strategy_id (default: registry default)')
    parser.add_argument('--out', required=True, help='directory for report.json and REPORT.md')
    parser.add_argument('--null-draws', type=int, default=NULL_DRAWS)
    args = parser.parse_args(argv)
    report = evaluate(args.dataset, args.strategy, null_draws=args.null_draws)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    (out / 'REPORT.md').write_text(render_markdown(report))
    print(json.dumps({'verdict': report['verdict'], 'balanced': report['summary']['balanced'],
                      'benchmark': report['benchmark_open_hold']['balanced']}, indent=2, ensure_ascii=False))
    return 0 if report['verdict'] in ('ACCEPT', 'PROVISIONAL') else 1


if __name__ == '__main__':
    raise SystemExit(main())
