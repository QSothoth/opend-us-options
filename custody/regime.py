"""Causal intraday regime dispatch; uses only the existing same-day features.

Evaluation labels are deliberately absent. State survives between completed
bars, while CustodyService separately owns the one-round-trip order lifecycle.
The scalar router is shared verbatim with the research JIT implementation.
"""
import copy
import hashlib
import json
import math

NAMES = ('chop', 'trend', 'pullback', 'recovery', 'adverse')
ROUTER_KEYS = ('efficiency', 'adx', 'max_crosses', 'confirmations',
               'recovery_minutes', 'pullback_atr', 'fixed_state', 'range_entry')
DEFAULT_ROUTER = [.30, 20., 3., 2., 15., .75, -1., 0.]


def route_step(f, p, state):
    """Update [active, pending, confirmations, adverse_age, recovery_age]."""
    if f[25] > 0:
        state[2] = 0.
        state[3] = 1000.
        state[4] = 1000.
    state[3] += 1.
    state[4] += 1.
    adverse = f[3] < 0 and f[4] < 0 and f[13] < 0
    if adverse:
        state[3] = 0.
    if state[3] <= 60 and (f[11] > 0 or f[29] > 0):
        state[4] = 0.
    aligned = f[3] > 0 and f[4] > 0
    quality = f[14] >= p[0] and f[7] >= p[1] and f[22] <= p[2]
    recovery = state[4] <= p[4] and f[3] > 0 and f[5] > 0 and f[12] > 0
    if adverse:
        proposed = 4
    elif recovery:
        proposed = 3
    elif aligned and quality and f[5] > 0 and f[13] > 0:
        proposed = 1
    elif aligned and -.5 <= f[21] <= p[5]:
        proposed = 2
    else:
        proposed = 0
    if proposed == int(state[1]):
        state[2] += 1.
    else:
        state[1], state[2] = float(proposed), 1.
    if state[2] >= p[3]:
        state[0] = float(proposed)
    if p[6] >= 0:
        state[0] = p[6]
    return int(state[0])


def validate_config(doc):
    from .adaptive import ENTRY_KEYS, EXIT_KEYS, validate_vectors
    if set(doc) != {'router', 'entries', 'exits'}:
        raise ValueError('regime config fields')
    if set(doc['router']) != set(ROUTER_KEYS):
        raise ValueError('regime router fields')
    p = [doc['router'][k] for k in ROUTER_KEYS]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in p):
        raise ValueError('invalid router value')
    if not 0 <= p[0] <= 1 or not 0 <= p[1] <= 100 or not 0 <= p[2] <= 20:
        raise ValueError('invalid regime threshold')
    if p[3] not in (1, 2, 3, 4) or not 1 <= p[4] <= 60 or not 0 <= p[5] <= 5:
        raise ValueError('invalid regime timing')
    if p[6] not in (-1, 0, 1, 2, 3, 4) or p[7] not in (0, 1):
        raise ValueError('invalid regime mode')
    if set(doc['entries']) != set(NAMES) or set(doc['exits']) != set(NAMES):
        raise ValueError('missing expert')
    deadlines, warmups, flatten = set(), set(), set()
    for name in NAMES:
        e, x = doc['entries'][name], doc['exits'][name]
        if set(e) != set(ENTRY_KEYS) or set(x) != set(EXIT_KEYS):
            raise ValueError('expert parameter fields')
        ev, xv = [e[k] for k in ENTRY_KEYS], [x[k] for k in EXIT_KEYS]
        validate_vectors(ev, xv)
        deadlines.add(e['deadline']); warmups.add(e['warmup']); flatten.add(x['flatten'])
    if len(deadlines) != 1 or len(warmups) != 1 or len(flatten) != 1:
        raise ValueError('experts must share custody time boundaries')


def vectors(doc):
    from .adaptive import ENTRY_KEYS, EXIT_KEYS
    validate_config(doc)
    return ([doc['router'][k] for k in ROUTER_KEYS],
            [[doc['entries'][name][k] for k in ENTRY_KEYS] for name in NAMES],
            [[doc['exits'][name][k] for k in EXIT_KEYS] for name in NAMES])


def make_strategy(profile, router, entries, exits, name=None):
    from .adaptive import make_strategy as make_base, ENTRY_KEYS, EXIT_KEYS
    item = make_base(profile, entries[0], exits[0])
    regime = dict(router=dict(zip(ROUTER_KEYS, router)),
                  entries={n:dict(zip(ENTRY_KEYS, e)) for n,e in zip(NAMES, entries)},
                  exits={n:dict(zip(EXIT_KEYS, x)) for n,x in zip(NAMES, exits)})
    validate_config(regime)
    item['config']['case']['entry']['regime'] = regime
    rule = {k:item['config']['case'][k] for k in ('entry', 'exit')}
    cid = hashlib.sha256(json.dumps(rule, sort_keys=True).encode()).hexdigest()[:12]
    item['config']['case']['id'] = cid
    item['config']['description'] = 'Same-day regime dispatch; one option round trip; no fixed profit target'
    raw = (json.dumps(item['config'], indent=2)+'\n').encode()
    item.update(strategy_id=name or 'custody_regime_'+cid, case_id=cid,
                sha256=hashlib.sha256(raw).hexdigest(), status='candidate_dryrun')
    return item


def regime_exit(job, frame):
    from datetime import date
    from .adaptive import exit_rule, EXIT_REASONS, EXIT_KEYS
    from .models import instant
    doc = job['strategy']['config']['case']['entry']['regime']
    active = int(frame.diagnostics['regime_id'])
    x = [doc['exits'][NAMES[active]][k] for k in EXIT_KEYS]
    f = frame.diagnostics['vector']
    sign = 1 if job['request']['direction'] == 'LONG' else -1
    opened = instant(job['opens'])
    t = (instant(job['entry_at'])-opened).total_seconds()/60
    entry = sign*job['entry_underlying']
    state = job.setdefault('adaptive_state', [entry, t, 0., -1e100])
    if job.get('exit_regime_id') != active:
        state[2] = 0.
    job['exit_regime_id'] = active
    dte = (date.fromisoformat(job['contract']['expiry'])-date.fromisoformat(job['request']['trade_date'])).days
    job['last_regime'] = NAMES[active]
    reason = exit_rule(f, x, state, entry, t, job['entry_atr'], dte)
    return EXIT_REASONS[reason] or None
