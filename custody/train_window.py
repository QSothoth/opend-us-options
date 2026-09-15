"""Build the expanded, full-window paired custody **raw/parent option cache**.

This builder produces the raw OpenD option cache (``custody-train-v2window-paired``);
it is **not** the formal train. Filter it to ``dte<=4`` (see ``CANONICAL_TRAIN_*``
below) for the canonical ``custody-train-dte4`` set, because the raw window contains
many long-DTE pairings once OpenD drops old weeklies. It reuses the real **underlying 1m**
already frozen in the ``eval-data-v2`` research Release (no underlying re-fetch)
and pairs each ``(symbol, session)`` with the same-day **option 1m** path fetched
once from read-only OpenD.

Contract policy
---------------
* The traded instrument is a single option per ``(symbol, session)``.
* Direction comes from a documented ``eval-data-v2`` scenario-label rule
  (``LONG`` buys CALL, ``SHORT`` buys PUT). The labels are case-definition
  metadata only and never enter the timing features.
* The expiry is the **nearest retrievable heavy-theta expiry**: same-day 0DTE
  where OpenD still has the contract (sessions from ``ZERO_DTE_FROM``), else the
  nearest still-retrievable weekly/monthly expiry (``2026-08-21`` for the
  earliest window sessions, rising to the current monthly ``2026-09-18``).
* The strike is the listed strike nearest the session's first underlying open
  that has real 1m bars for that session (near-ATM, never a synthetic strike).

Quota-aware: option chains are cached per ``(underlying, right)`` and history is
requested one series-day at a time behind a sliding-window limiter. Every
attempt is cached, including misses, so a strike/expiry is never probed twice.
Read-only ``OpenQuoteContext`` only; no trade API.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .marketdata import normalize_bar_rows
from .models import ET
from .opend import DEFAULT_HOST, DEFAULT_PORT
from .sliceio import sha256, write_series, write_slice_docs, zip_tree

# --- dataset identity ------------------------------------------------------
# ``custody-train-v2window-paired`` is the RAW/PARENT cache (long DTE included).
TRAIN_TAG = 'custody-train-v2window-paired'
ROLE = 'train/custody'
STARTER_ROLE = 'train/custody-starter'

# --- canonical/formal train location --------------------------------------
# The formal custody train is the frozen, offline-filtered DTE<=4 set, not the
# raw 830-case builder output. Absolute path (may live outside this repo):
CANONICAL_TRAIN_TAG = 'custody-train-dte4'
CANONICAL_TRAIN_MAX_DTE = 4
CANONICAL_TRAIN_DIR = '/workspace/pi-jobs/custody-train-dte4/out/custody-train-dte4'
PROSPECTIVE_FREEZE_NOTE = (
    "Freeze each session's DTE<=4 front-expiry paired underlying+option 1m bars the same day, "
    "while the chain still exists, and append them to the formal train (%s); OpenD drops old "
    'weeklies, so the canonical set only grows with a daily freeze.' % CANONICAL_TRAIN_DIR
)

# ``eval-data-v2`` 1m window (10 underlyings x 83 sessions = 830 symbol-sessions).
V2_SYMBOLS = [
    'US.SPY', 'US.QQQ', 'US.IWM', 'US.AAPL', 'US.NVDA',
    'US.TSLA', 'US.MU', 'US.AMD', 'US.META', 'US.MSFT',
]
V2_START = '2026-05-14'
V2_END = '2026-09-11'

# Strike universe is read from the currently-listed monthly chain; the actual
# traded contract may use an earlier expiry resolved by code construction.
STRIKE_REFERENCE_EXPIRY = '2026-09-18'

# Expiries known to have retrievable OpenD option history. Same-day 0DTE is
# available from 2026-08-17 onward; before that the nearest retrievable expiry
# is the 2026-08-21 weekly (data starts 2026-05-01).
ZERO_DTE_FROM = '2026-08-17'
FALLBACK_EXPIRIES = ['2026-08-21', '2026-08-28', '2026-09-04', '2026-09-11', '2026-09-18']

# Currently-listed expiry cadence: SPY/QQQ/IWM list every weekday; the single
# names list Mon/Wed/Fri. Historical cadence is verified per contract by real
# history probes (unsupported expiries fast-fail), these only order the probes.
DAILY_SYMBOLS = frozenset({'US.SPY', 'US.QQQ', 'US.IWM'})
WEEKLY_WEEKDAYS = frozenset({0, 2, 4})

DEFAULT_MAX_MONEYNESS = 0.05
DEFAULT_MIN_OPTION_BARS = 100
MAX_EXPIRY_TRIES = 6
MAX_STRIKE_TRIES = 14

# OpenD option-chain limit is 10 requests / 30s; space calls and cache.
CHAIN_MIN_INTERVAL = 3.2

_BULL = ('strong_up_trend', 'v_reversal_up', 'gap_up_open')
_BEAR = ('strong_down_trend', 'v_reversal_down', 'gap_down_open')


# ---------------------------------------------------------------------------
# eval-data-v2 loading (underlying 1m + scenario labels)
# ---------------------------------------------------------------------------
def _v2_et(epoch):
    """``eval-data-v2`` stores ET wall-clock as an epoch; recover the ET instant.

    09:31 ET is stored as ``09:31 UTC``. Treating the timestamp as UTC and
    dropping the zone yields the intended ET wall clock; attaching ET then gives
    a timestamp that lines up with OpenD's ``time_key`` bars.
    """
    naive = datetime.fromtimestamp(int(epoch), tz=timezone.utc).replace(tzinfo=None)
    return naive.replace(tzinfo=ET)


def load_v2_underlying(v2_dir, symbols=None):
    """Return ``{symbol: {session: [Bar, ...]}}`` from ``klines_1m.parquet``."""
    import pandas as pd

    path = Path(v2_dir) / 'klines_1m.parquet'
    if not path.exists():
        raise FileNotFoundError('eval-data-v2 klines_1m.parquet not found: ' + str(path))
    frame = pd.read_parquet(path)
    if 'ktype' in frame.columns:
        frame = frame[frame['ktype'] == 'K_1M']
    wanted = {s.upper() for s in symbols} if symbols else None
    out = {}
    for symbol, group in frame.groupby('symbol'):
        symbol = str(symbol).upper()
        if wanted is not None and symbol not in wanted:
            continue
        rows = []
        for row in group.itertuples(index=False):
            rows.append({
                'time_key': _v2_et(row.time).strftime('%Y-%m-%d %H:%M:%S'),
                'open': row.open, 'high': row.high, 'low': row.low,
                'close': row.close, 'volume': row.volume,
            })
        bars = normalize_bar_rows(rows, symbol, interval='1m', source='eval_v2')
        per_session = {}
        for bar in bars:
            per_session.setdefault(bar.close_time.astimezone(ET).date().isoformat(), []).append(bar)
        out[symbol] = per_session
    return out


def load_v2_labels(v2_dir):
    """Return ``{(symbol, session): {label: bool}}`` from scenario labels."""
    import pandas as pd

    path = Path(v2_dir) / 'scenario_labels.parquet'
    if not path.exists():
        return {}
    frame = pd.read_parquet(path)
    if 'grain' in frame.columns:
        frame = frame[frame['grain'] == 'session_1m']
    label_cols = ['strong_up_trend', 'strong_down_trend', 'range_chop', 'gap_up_open',
                  'gap_down_open', 'v_reversal_up', 'v_reversal_down', 'high_vol',
                  'low_vol', 'late_day_spike']
    out = {}
    for row in frame.to_dict(orient='records'):
        symbol = str(row.get('symbol', '')).upper()
        session = str(row.get('date', ''))
        if not symbol or not session:
            continue
        out[(symbol, session)] = {name: bool(row.get(name)) for name in label_cols}
    return out


def direction_from_labels(labels):
    """Documented direction rule: CALL on bullish labels, PUT on bearish ones.

    Priority resolves the (mutually exclusive within each pair) label families.
    Unlabelled sessions default to ``LONG`` so the case set stays paired.
    """
    if not labels:
        return 'LONG'
    if labels.get('strong_down_trend'):
        return 'SHORT'
    if labels.get('strong_up_trend'):
        return 'LONG'
    if labels.get('v_reversal_down') or labels.get('gap_down_open'):
        return 'SHORT'
    if labels.get('v_reversal_up') or labels.get('gap_up_open'):
        return 'LONG'
    return 'LONG'


def direction_basis(labels):
    """The label names that produced :func:`direction_from_labels` (for the case)."""
    if not labels:
        return ['default_long']
    if labels.get('strong_down_trend'):
        return ['strong_down_trend']
    if labels.get('strong_up_trend'):
        return ['strong_up_trend']
    down = [n for n in ('v_reversal_down', 'gap_down_open') if labels.get(n)]
    up = [n for n in ('v_reversal_up', 'gap_up_open') if labels.get(n)]
    if down:
        return down
    if up:
        return up
    return ['default_long']


# ---------------------------------------------------------------------------
# contract selection helpers
# ---------------------------------------------------------------------------
def _strike(row):
    value = row.get('strike_price') if hasattr(row, 'get') else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def option_code(symbol, expiry, right, strike):
    """Build a Futu US option code (``US.<sym><YYMMDD><C|P><strike*1000>``)."""
    ymd = date.fromisoformat(str(expiry)).strftime('%y%m%d')
    cp = 'C' if str(right).upper() == 'CALL' else 'P'
    return '%s%s%s%06d' % (str(symbol).upper(), ymd, cp, int(round(float(strike) * 1000)))


def candidate_strikes(reference, chain, max_moneyness=DEFAULT_MAX_MONEYNESS):
    """Ordered near-ATM strike candidates around ``reference``.

    The listed chain supplies the exact currently-listed strikes first (so the
    most likely valid strikes are probed first); a small generated grid covers
    expiry-specific increments (0DTE $1 vs early-weekly $5) afterwards. This
    never guesses an option code without probing real OpenD history.
    """
    reference = float(reference)
    if hasattr(chain, 'to_dict'):
        try:
            chain = chain.to_dict(orient='records')
        except TypeError:  # pragma: no cover - defensive
            chain = chain.to_dict('records')
    chain_strikes = set()
    for row in chain or []:
        strike = _strike(row)
        if strike and strike > 0:
            chain_strikes.add(round(strike, 4))
    generated = set()
    for inc in (0.5, 1.0, 2.5, 5.0, 10.0):
        anchor = round(reference / inc)
        for step in range(-6, 7):
            strike = round((anchor + step) * inc, 4)
            if strike > 0:
                generated.add(strike)

    def within(strike):
        return abs(strike - reference) / reference <= max_moneyness

    def distance(strike):
        return (abs(strike - reference), strike)

    primary = sorted((s for s in chain_strikes if within(s)), key=distance)
    secondary = sorted((s for s in generated if within(s) and s not in chain_strikes), key=distance)
    return primary + secondary


def expiry_candidates(session, symbol=None):
    """Nearest-first expiries that can still return history for ``session``.

    The same-day 0DTE (where the symbol lists it) and the known-retrievable
    weeklies are tried before the remaining matching weekdays so a symbol that
    lacks a near-dated listing does not burn the probe budget on non-existent
    daily expiries. Every candidate is still verified against real OpenD
    history; this only orders the attempts.
    """
    candidates = []
    if session >= ZERO_DTE_FROM:
        day = date.fromisoformat(session)
        daily = symbol is None or str(symbol).upper() in DAILY_SYMBOLS
        if daily:
            candidates.append(session)
        candidates.extend(sorted(e for e in FALLBACK_EXPIRIES if e >= session))
        for offset in range(0, 16):
            candidate = day + timedelta(days=offset)
            if candidate.weekday() >= 5:
                continue
            if daily or candidate.weekday() in WEEKLY_WEEKDAYS:
                candidates.append(candidate.isoformat())
    else:
        candidates.extend(sorted(e for e in FALLBACK_EXPIRIES if e >= session))
    ordered = []
    for candidate in candidates:
        if candidate >= session and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _quota(market):
    try:
        ret, data = market.context.get_history_kl_quota(get_detail=True)
    except Exception as exc:  # noqa: BLE001 - quota probe is best effort
        return {'error': str(exc)}
    if ret != 0:
        return {'error': str(data)}
    return {'used': int(data[0]), 'remain': int(data[1]), 'unique_symbols': len(data[2] or [])}


# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------
def build_v2window_train_slice(out_dir, v2_dir=None, start=V2_START, end=V2_END,
                               underlyings=None, market=None, host=DEFAULT_HOST,
                               port=DEFAULT_PORT, max_moneyness=DEFAULT_MAX_MONEYNESS,
                               min_option_bars=DEFAULT_MIN_OPTION_BARS,
                               max_cases=None, logger=print, rate_limit=True):
    """Reuse v2 underlying 1m and pair each symbol-session with an option 1m path."""
    underlyings = [s.upper() for s in (underlyings or V2_SYMBOLS)]
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    v2_dir = v2_dir or os.environ.get('CUSTODY_V2_DIR')

    underlying = load_v2_underlying(v2_dir, underlyings)
    labels = load_v2_labels(v2_dir)

    own_market = market is None
    if own_market:
        from .opend import OpenDMarket
        market = OpenDMarket(host=host, port=port)
    if not hasattr(market, 'option_chain'):
        if own_market:
            market.close()
        raise ValueError('windowed train builder requires a provider with option_chain()')

    limiter = _RateLimiter(50, 30.0) if rate_limit else None
    quota_before = _quota(market)

    sessions = sorted({day for symbol in underlyings
                       for day in underlying.get(symbol, {})
                       if start <= day <= end})

    chain_cache = {}
    fetch_cache = {}
    option_accum = {}
    expiry_ok = {}
    cases = []
    skipped = []
    chain_last = [0.0]

    def chain_for(symbol, right):
        key = (symbol, right)
        if key not in chain_cache:
            chain = []
            for attempt in range(4):
                wait = CHAIN_MIN_INTERVAL - (time.monotonic() - chain_last[0])
                if wait > 0:
                    time.sleep(wait)
                chain_last[0] = time.monotonic()
                try:
                    chain = market.option_chain(symbol, STRIKE_REFERENCE_EXPIRY, right)
                    if chain:
                        break
                    break
                except Exception as exc:  # noqa: BLE001
                    if 'frequency' in str(exc) and attempt < 3:
                        time.sleep(3.0 + 2.0 * attempt)
                        continue
                    chain = []
                    logger('chain unavailable for %s %s: %s' % (symbol, right, exc))
                    break
            chain_cache[key] = chain
        return chain_cache[key]

    def option_day(code, session):
        """Return ``(bars, status)`` for one contract/session; cached.

        ``status`` is ``'ok'`` (bars), ``'nodata'`` (contract exists but no
        session bars), ``'unknown'`` (contract not listed) or ``'error'``.
        """
        key = (code, session)
        if key not in fetch_cache:
            start_ts, end_ts = session + ' 00:00:00', session + ' 23:59:59'
            bars = []
            status = 'nodata'
            for attempt in range(5):
                if limiter is not None:
                    limiter.acquire()
                try:
                    bars = market.history_bars(code, 'K_1M', start_ts, end_ts)
                    status = 'ok' if bars else 'nodata'
                    break
                except Exception as exc:  # noqa: BLE001 - unknown/illiquid contract -> no pair
                    message = str(exc)
                    if 'frequency' in message and attempt < 4:
                        time.sleep(2.0 + 2.0 * attempt)
                        continue
                    status = 'unknown' if 'unknown' in message.lower() else 'error'
                    bars = []
                    break
            bars = [b for b in bars if b.close_time.astimezone(ET).date().isoformat() == session]
            dedup = {b.close_time: b for b in bars}
            fetch_cache[key] = ([dedup[k] for k in sorted(dedup)], status)
        return fetch_cache[key]

    try:
        for session in sessions:
            for symbol in underlyings:
                day_bars = underlying.get(symbol, {}).get(session)
                if not day_bars:
                    skipped.append({'symbol': symbol, 'trade_date': session, 'reason': 'no v2 underlying 1m'})
                    continue
                reference = day_bars[0].open
                label = labels.get((symbol, session))
                direction = direction_from_labels(label)
                right = 'CALL' if direction == 'LONG' else 'PUT'
                chain = chain_for(symbol, right)
                strikes = candidate_strikes(reference, chain, max_moneyness)

                # Prefer expiries already observed to carry data for this symbol
                # (still reset per symbol); otherwise keep nearest-first order.
                candidates = expiry_candidates(session, symbol)
                known_good = expiry_ok.get(symbol, set())
                candidates = ([e for e in candidates if e in known_good]
                              + [e for e in candidates if e not in known_good])

                chosen = None
                partial = None
                for expiry in candidates[:MAX_EXPIRY_TRIES]:
                    unknown_streak = 0
                    for strike in strikes[:MAX_STRIKE_TRIES]:
                        code = option_code(symbol, expiry, right, strike)
                        bars, status = option_day(code, session)
                        if len(bars) >= min_option_bars:
                            chosen = (expiry, code, strike, bars)
                            break
                        if bars and partial is None:
                            partial = (expiry, code, strike, bars)
                        if status == 'unknown':
                            # Several consecutive unlisted strikes mean this
                            # expiry is not offered for the symbol; skip it.
                            unknown_streak += 1
                            if unknown_streak >= 6:
                                break
                        else:
                            unknown_streak = 0
                    if chosen:
                        break
                if chosen is None:
                    chosen = partial
                if chosen is None:
                    skipped.append({'symbol': symbol, 'trade_date': session,
                                    'reason': 'no near-ATM %s option 1m in retrievable expiries' % right})
                    continue
                expiry, code, strike, bars = chosen
                expiry_ok.setdefault(symbol, set()).add(expiry)
                option_accum.setdefault(code, {})
                for bar in bars:
                    option_accum[code][bar.close_time] = bar
                cases.append({
                    'symbol': symbol,
                    'direction': direction,
                    'contract': code,
                    'trade_date': session,
                    'expiry': expiry,
                    'right': right,
                    'strike': strike,
                    'reference_price': reference,
                    'dte': (date.fromisoformat(expiry) - date.fromisoformat(session)).days,
                    'underlying_bar_count': len(day_bars),
                    'option_bar_count': len(bars),
                    'direction_basis': direction_basis(label),
                    'scenario_labels': sorted(name for name, on in (label or {}).items() if on),
                })
                if logger:
                    logger('case %s %s %s %s expiry %s strike %s under=%d option=%d' % (
                        session, symbol, direction, code, expiry, strike,
                        len(day_bars), len(bars)))
                if max_cases and len(cases) >= max_cases:
                    break
            if max_cases and len(cases) >= max_cases:
                break
    finally:
        if own_market:
            market.close()

    series = []
    for symbol in underlyings:
        bars = []
        for day_bars in underlying.get(symbol, {}).values():
            bars.extend(day_bars)
        dedup = {b.close_time: b for b in bars}
        ordered = [dedup[k] for k in sorted(dedup)]
        if ordered:
            series.append(write_series(root, 'underlying', symbol, ordered))
    for code, by_time in option_accum.items():
        ordered = [by_time[k] for k in sorted(by_time)]
        series.append(write_series(root, 'option', code, ordered))

    covered_sessions = sorted({c['trade_date'] for c in cases})
    covered_symbols = sorted({c['symbol'] for c in cases})
    quota_after = _quota(market) if own_market else _quota(market)
    dataset = TRAIN_TAG
    cases_doc = {
        'schema_version': 1, 'dataset': dataset, 'role': ROLE, 'paired': True,
        'required_series': ['underlying', 'option'],
        'sessions': covered_sessions, 'underlyings': covered_symbols, 'cases': cases,
    }
    manifest = {
        'schema_version': 1,
        'dataset': dataset,
        'role': ROLE,
        'role_note': ('raw/parent paired OpenD option cache over the eval-data-v2 1m window; NOT the formal train; '
                      'filter to dte<=4 for the canonical custody-train-dte4 set because long-DTE pairings appear '
                      'once OpenD drops old weeklies. underlying 1m reused from eval-data-v2, option 1m fetched from read-only OpenD'),
        'timezone': 'America/New_York',
        'interval': '1m',
        'paired': True,
        'required_series': ['underlying', 'option'],
        'success_metric': 'option_pnl',
        'option_multiplier': 100,
        'opend_host': host,
        'opend_port': int(port),
        'fetched_at': datetime.now(ET).isoformat(),
        'underlying_source': 'eval-data-v2 klines_1m.parquet (underlying-only research Release)',
        'option_source': 'read-only OpenD 127.0.0.1:11111 K_1M trade history',
        'window': {'start': start, 'end': end},
        'sessions': covered_sessions,
        'underlyings': covered_symbols,
        'v2_symbols': underlyings,
        'v2_symbol_sessions': sum(len(underlying.get(s, {})) for s in underlyings),
        'case_count': len(cases),
        'series_count': len(series),
        'skipped_count': len(skipped),
        'skipped': skipped,
        'contract_selection': ('nearest retrievable heavy-theta expiry per session (same-day 0DTE or the next '
                               'retrievable weekly; 2026-08-21 for the earliest window sessions because earlier '
                               'OpenD option history is gone) with the listed strike nearest the session '
                               'first-bar underlying open that has real 1m bars'),
        'direction_policy': ('eval-data-v2 session_1m scenario labels: strong_down/v_reversal_down/'
                             'gap_down -> SHORT (PUT); strong_up/v_reversal_up/gap_up -> LONG (CALL); '
                             'else LONG. Labels define the case direction only, never timing features.'),
        'price_note': ('Underlying and option series are OpenD/eval-data-v2 1m trade OHLCV, not NBBO. '
                       'Live uses option bid/ask quotes through the same provider boundary.'),
        'metric_note': ('Custody success is measured on the option contract path/fills only; '
                        'underlying-proxy payoff is forbidden.'),
        'validation_reference': 'custody-eval-2026-09-14',
        'quota': {'before': quota_before, 'after': quota_after},
        'expiry_histogram': _histogram(c['expiry'] for c in cases),
        'dte_histogram': _histogram(c['dte'] for c in cases),
        'series': series,
        'cases': cases,
    }
    write_slice_docs(root, cases_doc, manifest)
    _write_checksums(root, series)
    zip_path = zip_tree(root)
    digest = sha256(zip_path)
    (root.parent / (root.name + '.zip.sha256')).write_text('%s  %s\n' % (digest, zip_path.name))
    # The zip hash is returned in-memory only: writing it back into manifest.json
    # would invalidate CHECKSUMS.sha256 and the just-computed zip digest.
    manifest['zip'] = {'path': str(zip_path), 'sha256': digest}
    if logger:
        logger('frozen %d cases / %d series -> %s (zip sha256 %s)' % (
            len(cases), len(series), root, digest))
    return manifest


def _histogram(values):
    out = {}
    for value in values:
        key = str(value)
        out[key] = out.get(key, 0) + 1
    return out


def repackage_slice(root, logger=print):
    """Rebuild manifest checksums + zip for an already-frozen slice directory.

    Used when a slice was packaged by an older builder that wrote the in-memory
    ``zip`` block back into ``manifest.json`` (which invalidates the checksums).
    The on-disk series/cases are untouched.
    """
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    manifest.pop('zip', None)
    manifest['case_count'] = len(manifest.get('cases', []))
    manifest['series_count'] = len(manifest.get('series', []))
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    _write_checksums(root, manifest.get('series', []))
    zip_path = zip_tree(root)
    digest = sha256(zip_path)
    (root.parent / (root.name + '.zip.sha256')).write_text('%s  %s\n' % (digest, zip_path.name))
    manifest['zip'] = {'path': str(zip_path), 'sha256': digest}
    if logger:
        logger('repackaged %s -> %s (zip sha256 %s)' % (root, zip_path, digest))
    return manifest


def _write_checksums(root, series):
    lines = []
    for item in series:
        for rel, digest in (item.get('sha256') or {}).items():
            lines.append('%s  %s' % (digest, item[rel]))
    lines.append('%s  %s' % (sha256(Path(root) / 'cases.json'), 'cases.json'))
    lines.append('%s  %s' % (sha256(Path(root) / 'manifest.json'), 'manifest.json'))
    (Path(root) / 'CHECKSUMS.sha256').write_text('\n'.join(lines) + '\n')


class _RateLimiter:
    """Small blocking sliding-window limiter (OpenD history K-line cap)."""

    def __init__(self, max_calls, window_seconds):
        from collections import deque
        self.max_calls = int(max_calls)
        self.window_seconds = float(window_seconds)
        self._calls = deque()

    def acquire(self):
        while True:
            now = time.monotonic()
            horizon = now - self.window_seconds
            while self._calls and self._calls[0] <= horizon:
                self._calls.popleft()
            if len(self._calls) < self.max_calls:
                self._calls.append(now)
                return
            time.sleep(max(0.0, self.window_seconds - (now - self._calls[0]) + 0.01))


def build_argument_parser():
    import argparse
    parser = argparse.ArgumentParser(
        prog='custody fetch-train',
        description='Build the expanded paired raw/parent option cache (eval-data-v2 window + read-only OpenD); filter to DTE<=4 for the formal train.')
    parser.add_argument('--out', required=True,
                        help='raw/parent option-cache output directory, e.g. '
                             'out/custody-train-v2window-paired (formal DTE<=4 train is filtered '
                             'to %s)' % CANONICAL_TRAIN_DIR)
    parser.add_argument('--v2-dir', default=None,
                        help='eval-data-v2 directory (klines_1m.parquet + scenario_labels.parquet); '
                             'defaults to $CUSTODY_V2_DIR')
    parser.add_argument('--start', default=V2_START, help='first ET session (default %s)' % V2_START)
    parser.add_argument('--end', default=V2_END, help='last ET session (default %s)' % V2_END)
    parser.add_argument('--underlyings', default=','.join(V2_SYMBOLS),
                        help='comma-separated underlyings (default: v2 symbols)')
    parser.add_argument('--max-cases', type=int, default=None, help='stop after N cases (debug)')
    parser.add_argument('--min-option-bars', type=int, default=DEFAULT_MIN_OPTION_BARS)
    parser.add_argument('--max-moneyness', type=float, default=DEFAULT_MAX_MONEYNESS)
    parser.add_argument('--host', default=DEFAULT_HOST)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--log', default=None, help='append progress lines to this file')
    return parser


def main(argv=None):
    args = build_argument_parser().parse_args(argv)
    handle = None
    def log(message):
        print(message, flush=True)
        if handle is not None:
            handle.write(str(message) + '\n')
            handle.flush()
    if args.log:
        handle = open(args.log, 'a')
    from .opend import OpenDMarket
    market = OpenDMarket(host=args.host, port=args.port)
    try:
        manifest = build_v2window_train_slice(
            args.out,
            v2_dir=args.v2_dir,
            start=args.start,
            end=args.end,
            underlyings=[s.strip() for s in args.underlyings.split(',') if s.strip()],
            market=market,
            host=args.host,
            port=args.port,
            max_moneyness=args.max_moneyness,
            min_option_bars=args.min_option_bars,
            max_cases=args.max_cases,
            logger=log,
        )
    finally:
        market.close()
        if handle is not None:
            handle.close()
    print(json.dumps({
        'dataset': manifest['dataset'], 'role': manifest['role'],
        'case_count': manifest['case_count'], 'series_count': manifest['series_count'],
        'sessions': [manifest['sessions'][0], manifest['sessions'][-1]] if manifest['sessions'] else [],
        'underlyings': manifest['underlyings'], 'skipped_count': manifest['skipped_count'],
        'quota': manifest['quota'], 'zip': manifest.get('zip'),
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
