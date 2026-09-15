"""Build the formal **TRUE-0DTE** paired custody train slice.

The custody product is a 末日 (same-day expiry) product. The earlier formal train
``custody-train-dte4`` mixed DTE 0…4, but non-0DTE and 0DTE trade very
differently: non-0DTE option fills are often poor while 0DTE volume is high and
fillable. This builder keeps **only** cases where ``expiry == trade_date``
(DTE = 0) and never pads with DTE 1…4 just to grow the case count.

Universe (priority order)
-------------------------
``US.SPY``, ``US.QQQ``, ``US.IWM`` first (liquid daily 0DTE where OpenD still
retains the same-day chain), then the mega-cap / hot single names **only** on
sessions where OpenD has that same-day expiry and the near-ATM option
day-volume clears the documented single-name floor (``US.AAPL``, ``US.MSFT``,
``US.NVDA``, ``US.TSLA``, ``US.META``, ``US.AMZN``, ``US.GOOGL`` plus
``US.AMD``/``US.MU`` when a same-day expiry exists). Single names are never
forced onto sessions that lack a 0DTE chain.

Two-tier volume floor and diversification
-----------------------------------------
The index ETFs are far deeper than single names, so one SPY-sized floor silently
drops every Mag7 case. This builder documents **two** floors: a high index tier
(SPY/QQQ/IWM) and a lower, still-liquid single-name tier. It also caps the SPY
share of the frozen cases so the train does not collapse back to an
index-only, SPY-dominated set; held-out SPY sessions are recorded in the
manifest (``spy_cap_dropped``) rather than silently discarded.

Contract policy
---------------
* one contract per ``(symbol, session)``;
* the near-ATM listed strike nearest the session first-bar underlying open, with
  **both** the CALL and PUT ATM series fetched;
* the traded right is the side with the **higher same-day option volume**
  (LONG buys the CALL, SHORT buys the PUT) — a documented, volume-driven rule,
  not a scenario label;
* cases whose chosen contract day-volume is below the floor are dropped.

Quota / OpenD retention
-----------------------
OpenD retains historical option 1m history only for expiry groups that were
fetched while the chain existed. Its option-history quota is accounted per
``(underlying, expiry)`` group, so a *new* expiry group cannot be fetched once
the account quota is spent, while new strikes inside an already-warm group can.
The builder only targets warm same-day expiry groups (``warm_option_groups``)
and records every skipped session with its reason. Underlying 1m is reused from
the ``eval-data-v2`` Release where present and fetched read-only from OpenD for
the later sessions.

Read-only ``OpenQuoteContext`` only; never a trade API.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

from .models import ET
from .opend import DEFAULT_HOST, DEFAULT_PORT
from .sliceio import sha256, write_series, write_slice_docs, zip_tree
from .train_window import (candidate_strikes, load_v2_underlying, option_code,
                           _RateLimiter, _histogram)

# --- dataset identity ------------------------------------------------------
TRAIN_TAG = 'custody-train-0dte'
ROLE = 'train/custody'
# Archive of the previous mixed-DTE formal train (no longer canonical).
DTE4_ARCHIVE_TAG = 'custody-train-dte4'
DTE4_ARCHIVE_DIR = '/workspace/pi-jobs/custody-train-dte4/out/custody-train-dte4'

# --- target set ------------------------------------------------------------
# ~1 calendar month of US sessions (mid/late Aug -> latest recoverable 0DTE).
WINDOW_START = '2026-08-17'
WINDOW_END = '2026-09-14'
# Index ETFs (deep daily 0DTE) are the high-liquidity tier.
INDEX_SYMBOLS = ['US.SPY', 'US.QQQ', 'US.IWM']
# 七姐妹 / Mag7-style mega-caps, plus AMD/MU when a same-day expiry exists.
# These are below SPY-sized volume but still liquid at the single-name floor.
MEGACAP_SYMBOLS = ['US.AAPL', 'US.MSFT', 'US.NVDA', 'US.TSLA', 'US.META',
                   'US.AMZN', 'US.GOOGL']
OTHER_HOT_SYMBOLS = ['US.AMD', 'US.MU']
SINGLE_NAME_SYMBOLS = MEGACAP_SYMBOLS + OTHER_HOT_SYMBOLS
PRIORITY_SYMBOLS = INDEX_SYMBOLS
OPTIONAL_SYMBOLS = SINGLE_NAME_SYMBOLS
DEFAULT_SYMBOLS = INDEX_SYMBOLS + SINGLE_NAME_SYMBOLS
# The index floor is derived from the pooled index-ETF day-volumes.
FLOOR_SYMBOLS = INDEX_SYMBOLS

DEFAULT_MAX_MONEYNESS = 0.05
DEFAULT_MIN_OPTION_BARS = 300
DEFAULT_MAX_STRIKE_TRIES = 14
# Two-tier auto volume floor. Index tier: 10% of the pooled index-ETF ATM 0DTE
# median day-volume, rounded down to a round thousand. Single-name tier: 10% of
# the index floor (minimum 1,000) so Mag7/hot names are never dropped merely for
# being below SPY-sized volume.
INDEX_FLOOR_FRACTION = 0.10
FLOOR_FRACTION = INDEX_FLOOR_FRACTION  # back-compat alias
SINGLE_FLOOR_FRACTION = 0.10
SINGLE_FLOOR_MIN = 1000
# Keep SPY from dominating the train mix: cap its share of the final cases.
SPY_SHARE_CAP = 0.35

_WARM_GROUP_RE = re.compile(r'\[opt exp (\d{4}-\d{2}-\d{2})\]')


# ---------------------------------------------------------------------------
# warm expiry groups (quota-aware)
# ---------------------------------------------------------------------------
def warm_option_groups(market):
    """Return ``{(symbol, expiry)}`` already warm in the OpenD option quota.

    OpenD accounts option history per ``(underlying, expiry)`` group: a group
    fetched once can serve new strikes, but a brand-new expiry group is refused
    once the 7-day option quota is spent. Parsing the quota detail lets the
    builder ask only for recoverable same-day expiries instead of burning
    probes (and quota errors) on groups that are no longer retrievable.
    """
    ret, data = market.context.get_history_kl_quota(get_detail=True)
    if ret != 0:
        raise RuntimeError('get_history_kl_quota failed: ' + str(data))
    groups = set()
    for row in data[2] or []:
        if not hasattr(row, 'get'):
            continue
        symbol = str(row.get('code', '')).strip().upper()
        match = _WARM_GROUP_RE.search(str(row.get('name', '')))
        if symbol and match:
            groups.add((symbol, match.group(1)))
    return groups


def zero_dte_targets(warm_groups, symbols=None, start=WINDOW_START, end=WINDOW_END):
    """Ordered ``[(symbol, session)]`` for recoverable true-0DTE groups.

    Only groups whose expiry lies inside the window are kept; the expiry *is*
    the trade date, so every returned target is DTE 0 by construction.
    Symbols keep the documented priority order.
    """
    symbols = [str(s).upper() for s in (symbols or DEFAULT_SYMBOLS)]
    warm = {(str(s).upper(), str(d)) for s, d in warm_groups}
    targets = []
    for symbol in symbols:
        sessions = sorted(d for s, d in warm if s == symbol and start <= d <= end)
        for session in sessions:
            targets.append((symbol, session))
    return targets


# ---------------------------------------------------------------------------
# volume floor
# ---------------------------------------------------------------------------
def derive_volume_floor(core_volumes, fraction=FLOOR_FRACTION):
    """Round-down **index-tier** volume floor from the index ATM 0DTE volumes.

    ``core_volumes`` are the chosen-contract day-volumes of the index-tier
    underlyings (SPY/QQQ/IWM). The floor is ``fraction`` of their median,
    rounded down to a round thousand, so a floor is never an arbitrary constant
    divorced from the data. Returns ``None`` when there are no core volumes.
    """
    values = sorted(float(v) for v in core_volumes if v is not None and v > 0)
    if not values:
        return None
    middle = len(values) // 2
    median = (values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2.0)
    floor = int((median * float(fraction)) // 1000 * 1000)
    return floor if floor > 0 else 1000


def pooled_median(values):
    ordered = sorted(float(v) for v in values if v is not None and v > 0)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0


def derive_single_name_floor(index_floor, fraction=SINGLE_FLOOR_FRACTION,
                             floor_min=SINGLE_FLOOR_MIN):
    """Lower, still-liquid single-name floor derived from the index floor.

    Single-name 0DTE is far thinner than SPY/QQQ, so the tier floor is
    ``fraction`` of the index floor, rounded down to a round thousand and
    bounded below by ``floor_min``. That keeps Mag7/hot names in the train while
    still requiring real same-day option volume.
    """
    if not index_floor or float(index_floor) <= 0:
        return int(floor_min)
    floor = int((float(index_floor) * float(fraction)) // 1000 * 1000)
    return max(floor, int(floor_min))


def apply_spy_share_cap(cases, cap=SPY_SHARE_CAP, symbol='US.SPY'):
    """Return ``(kept, dropped)`` limiting the SPY share of the final cases.

    SPY is the deepest 0DTE underlying, so an unfiltered build is SPY-heavy. To
    keep the train diversified we hold out the *lowest-day-volume* SPY sessions
    until ``symbol`` is no more than ``cap`` of the remaining cases. All
    non-SPY cases are always kept, the cap is only applied when other
    underlyings are present, and at least one SPY case is retained when any
    exist. ``dropped`` rows are returned so nothing is silently lost.
    """
    cases = list(cases)
    if not cases or cap is None or float(cap) <= 0 or float(cap) >= 1:
        return cases, []
    wanted = str(symbol).upper()
    spy = [c for c in cases if str(c['symbol']).upper() == wanted]
    others = [c for c in cases if str(c['symbol']).upper() != wanted]
    if not spy or not others:
        return cases, []
    if len(spy) / float(len(cases)) <= float(cap):
        return cases, []
    keep_count = int((float(cap) * len(others)) / (1.0 - float(cap)))
    keep_count = max(1, min(len(spy), keep_count))
    ordered = sorted(spy, key=lambda c: (-float(c['chosen_volume']), c['trade_date']))
    kept_spy = ordered[:keep_count]
    dropped = ordered[keep_count:]
    kept = others + kept_spy
    kept.sort(key=lambda c: (c['trade_date'], c['symbol']))
    return kept, dropped


# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------
def build_0dte_train_slice(out_dir, v2_dir=None, start=WINDOW_START, end=WINDOW_END,
                           symbols=None, market=None, host=DEFAULT_HOST,
                           port=DEFAULT_PORT, volume_floor=None,
                           single_name_floor=None, spy_share_cap=SPY_SHARE_CAP,
                           min_option_bars=DEFAULT_MIN_OPTION_BARS,
                           max_moneyness=DEFAULT_MAX_MONEYNESS,
                           warm_groups=None, logger=print, rate_limit=True):
    """Fetch near-ATM CALL/PUT 0DTE pairs and freeze the true-0DTE train slice."""
    symbols = [str(s).upper() for s in (symbols or DEFAULT_SYMBOLS)]
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    v2_dir = v2_dir or os.environ.get('CUSTODY_V2_DIR')
    underlying = load_v2_underlying(v2_dir, symbols) if v2_dir else {}

    own_market = market is None
    if own_market:
        from .opend import OpenDMarket
        market = OpenDMarket(host=host, port=port)

    if warm_groups is None:
        warm_groups = warm_option_groups(market)
    targets = zero_dte_targets(warm_groups, symbols, start, end)

    limiter = _RateLimiter(50, 30.0) if rate_limit else None
    option_fetch_cache = {}
    underlying_fetch_cache = {}
    gaps = []

    def option_session_bars(code, session):
        key = (str(code).upper(), session)
        if key not in option_fetch_cache:
            start_ts, end_ts = session + ' 00:00:00', session + ' 23:59:59'
            bars, error = [], None
            for attempt in range(5):
                if limiter is not None:
                    limiter.acquire()
                try:
                    bars = market.history_bars(code, 'K_1M', start_ts, end_ts)
                    break
                except Exception as exc:  # noqa: BLE001 - missing/quota-limited contract
                    message = str(exc)
                    if 'frequency' in message and attempt < 4:
                        time.sleep(2.0 + 2.0 * attempt)
                        continue
                    bars, error = [], message
                    break
            bars = [b for b in bars
                    if str(b.code).upper() == str(code).upper()
                    and b.close_time.astimezone(ET).date().isoformat() == session]
            dedup = {b.close_time: b for b in bars}
            option_fetch_cache[key] = ([dedup[k] for k in sorted(dedup)], error)
        return option_fetch_cache[key]

    def underlying_session_bars(symbol, session):
        cached = underlying.get(symbol, {}).get(session)
        if cached:
            return list(cached)
        key = (symbol, session)
        if key not in underlying_fetch_cache:
            start_ts, end_ts = session + ' 00:00:00', session + ' 23:59:59'
            if limiter is not None:
                limiter.acquire()
            try:
                bars = market.history_bars(symbol, 'K_1M', start_ts, end_ts)
            except Exception as exc:  # noqa: BLE001 - report missing underlying
                gaps.append({'symbol': symbol, 'trade_date': session,
                             'reason': 'underlying 1m fetch failed: ' + str(exc)})
                bars = []
            bars = [b for b in bars
                    if str(b.code).upper() == symbol.upper()
                    and b.close_time.astimezone(ET).date().isoformat() == session]
            dedup = {b.close_time: b for b in bars}
            underlying_fetch_cache[key] = [dedup[k] for k in sorted(dedup)]
        return list(underlying_fetch_cache[key])

    raw_cases = []
    try:
        for symbol, session in targets:
            ubars = underlying_session_bars(symbol, session)
            if not ubars:
                gaps.append({'symbol': symbol, 'trade_date': session,
                             'reason': 'no same-day underlying 1m bars'})
                continue
            reference = ubars[0].open
            strikes = candidate_strikes(reference, [], max_moneyness)
            chosen = None
            fallback = None
            last_error = None
            for strike in strikes[:DEFAULT_MAX_STRIKE_TRIES]:
                call_code = option_code(symbol, session, 'CALL', strike)
                put_code = option_code(symbol, session, 'PUT', strike)
                call_bars, call_error = option_session_bars(call_code, session)
                put_bars, put_error = option_session_bars(put_code, session)
                if call_error or put_error:
                    last_error = (call_error or put_error)[:160]
                call_ok = len(call_bars) >= min_option_bars
                put_ok = len(put_bars) >= min_option_bars
                if call_ok and put_ok:
                    chosen = (strike, call_code, call_bars, put_code, put_bars)
                    break
                if (call_ok or put_ok) and fallback is None:
                    fallback = (strike, call_code, call_bars, put_code, put_bars)
            if chosen is None:
                chosen = fallback
            if chosen is None:
                gaps.append({'symbol': symbol, 'trade_date': session,
                             'reason': ('no near-ATM 0DTE CALL/PUT pair with >= %d bars; last error: %s'
                                        % (min_option_bars, last_error))
                             if last_error else
                             ('no near-ATM 0DTE CALL/PUT pair with >= %d bars' % min_option_bars)})
                continue
            strike, call_code, call_bars, put_code, put_bars = chosen
            call_volume = float(sum(b.volume for b in call_bars))
            put_volume = float(sum(b.volume for b in put_bars))
            if call_volume >= put_volume and call_bars:
                right, code, bars, chosen_volume, other_volume = (
                    'CALL', call_code, call_bars, call_volume, put_volume)
                direction = 'LONG'
            else:
                right, code, bars, chosen_volume, other_volume = (
                    'PUT', put_code, put_bars, put_volume, call_volume)
                direction = 'SHORT'
            raw_cases.append({
                'symbol': symbol, 'direction': direction, 'contract': code,
                'trade_date': session, 'expiry': session, 'right': right, 'strike': strike,
                'reference_price': reference,
                'call_contract': call_code, 'put_contract': put_code,
                'call_volume': call_volume, 'put_volume': put_volume,
                'chosen_volume': chosen_volume, 'other_volume': other_volume,
                'underlying_bar_count': len(ubars), 'option_bar_count': len(bars),
                'underlying_bars': ubars, 'option_bars': bars,
            })
            if logger:
                logger('0DTE %s %s %s %s strike %s call_vol=%.0f put_vol=%.0f' % (
                    session, symbol, direction, code, strike, call_volume, put_volume))
    finally:
        if own_market:
            market.close()

    core_volumes = [c['chosen_volume'] for c in raw_cases if c['symbol'] in FLOOR_SYMBOLS]
    auto_floor = volume_floor is None
    index_floor = volume_floor if volume_floor is not None else derive_volume_floor(core_volumes)
    if index_floor is None:
        index_floor = 0
    if single_name_floor is None:
        single_name_floor = derive_single_name_floor(index_floor)

    def _tier_floor(symbol):
        return (single_name_floor if str(symbol).upper() in SINGLE_NAME_SYMBOLS
                else index_floor)

    kept = [c for c in raw_cases if c['chosen_volume'] >= _tier_floor(c['symbol'])]
    filtered = [{'symbol': c['symbol'], 'trade_date': c['trade_date'],
                 'contract': c['contract'], 'chosen_volume': c['chosen_volume'],
                 'floor': _tier_floor(c['symbol']),
                 'reason': 'below %s volume floor %d' % (
                     'single-name' if c['symbol'] in SINGLE_NAME_SYMBOLS else 'index',
                     _tier_floor(c['symbol']))}
                for c in raw_cases if c['chosen_volume'] < _tier_floor(c['symbol'])]

    capped, spy_cap_dropped_cases = apply_spy_share_cap(kept, cap=spy_share_cap)
    spy_cap_dropped = [{'symbol': c['symbol'], 'trade_date': c['trade_date'],
                        'contract': c['contract'], 'chosen_volume': c['chosen_volume'],
                        'reason': 'SPY held out to keep SPY share <= %.0f%%' % (
                            100.0 * float(spy_share_cap))}
                       for c in spy_cap_dropped_cases]

    option_accum = {}
    underlying_accum = {}
    cases = []
    for case in capped:
        option_accum.setdefault(case['contract'], {})
        for bar in case['option_bars']:
            option_accum[case['contract']][bar.close_time] = bar
        underlying_accum.setdefault(case['symbol'], {})
        for bar in case['underlying_bars']:
            underlying_accum[case['symbol']][bar.close_time] = bar
        cases.append({key: case[key] for key in (
            'symbol', 'direction', 'contract', 'trade_date', 'expiry', 'right', 'strike',
            'reference_price', 'call_contract', 'put_contract', 'call_volume', 'put_volume',
            'chosen_volume', 'underlying_bar_count', 'option_bar_count')}
            | {'dte': 0})

    series = []
    for symbol, by_time in underlying_accum.items():
        ordered = [by_time[k] for k in sorted(by_time)]
        if ordered:
            series.append(write_series(root, 'underlying', symbol, ordered))
    for code, by_time in option_accum.items():
        ordered = [by_time[k] for k in sorted(by_time)]
        series.append(write_series(root, 'option', code, ordered))

    covered_sessions = sorted({c['trade_date'] for c in cases})
    covered_symbols = sorted({c['symbol'] for c in cases})
    total_cases = len(cases)

    def _counts(rows, key):
        out = {}
        for row in rows:
            out[row[key]] = out.get(row[key], 0) + 1
        return out

    def _share(count):
        return round(count / float(total_cases), 4) if total_cases else 0.0

    case_counts_by_symbol = _counts(cases, 'symbol')
    case_counts_by_tier = {
        'index_etf': sum(1 for c in cases if c['symbol'] in INDEX_SYMBOLS),
        'single_name': sum(1 for c in cases if c['symbol'] in SINGLE_NAME_SYMBOLS),
    }
    spy_count = case_counts_by_symbol.get('US.SPY', 0)
    spy_share = _share(spy_count)
    single_name_share = _share(case_counts_by_tier['single_name'])
    index_share = _share(case_counts_by_tier['index_etf'])
    requested_symbols = [str(s).upper() for s in (symbols or DEFAULT_SYMBOLS)]
    unavailable_symbols = [s for s in requested_symbols if s not in covered_symbols]
    volume_floor_basis = (
        'two-tier auto: index floor = %.0f%% of the pooled %s chosen-contract ATM 0DTE '
        'median day-volume (median %.0f -> %d); single-name floor = %.0f%% of the index '
        'floor (min %d -> %d)'
        % (100 * INDEX_FLOOR_FRACTION, '/'.join(FLOOR_SYMBOLS),
           pooled_median(core_volumes) or 0, index_floor,
           100 * SINGLE_FLOOR_FRACTION, SINGLE_FLOOR_MIN, single_name_floor)
        if auto_floor else
        'caller-supplied absolute index floor %d / single-name floor %d contracts/day'
        % (index_floor, single_name_floor))
    cases_doc = {
        'schema_version': 1, 'dataset': TRAIN_TAG, 'role': ROLE, 'paired': True,
        'required_series': ['underlying', 'option'],
        'max_dte': 0, 'sessions': covered_sessions, 'underlyings': covered_symbols,
        'volume_floor': index_floor, 'volume_floor_index': index_floor,
        'volume_floor_single': single_name_floor,
        'cases': cases,
    }
    manifest = {
        'schema_version': 1,
        'dataset': TRAIN_TAG,
        'role': ROLE,
        'role_note': ('formal custody train: TRUE 0DTE only (expiry == trade_date, DTE=0). '
                      'No DTE 1-4 padding. Liquid index ETFs SPY/QQQ/IWM plus the Mag7-style '
                      'mega-caps (AAPL/MSFT/NVDA/TSLA/META; AMZN/GOOGL only where a warm '
                      'same-day expiry exists) and AMD/MU, each kept only on a same-day expiry '
                      'that clears its tier volume floor. A two-tier floor keeps single names '
                      'that are below SPY-sized volume but still liquid, and a SPY share cap '
                      'holds the set diversified. The mixed-DTE custody-train-dte4 set is archive.'),
        'timezone': 'America/New_York',
        'interval': '1m',
        'paired': True,
        'required_series': ['underlying', 'option'],
        'success_metric': 'option_pnl',
        'option_multiplier': 100,
        'max_dte': 0,
        'dte_histogram': {'0': len(cases)},
        'opend_host': host,
        'opend_port': int(port),
        'fetched_at': datetime.now(ET).isoformat(),
        'window': {'start': start, 'end': end},
        'sessions': covered_sessions,
        'underlyings': covered_symbols,
        'priority_symbols': INDEX_SYMBOLS,
        'index_symbols': INDEX_SYMBOLS,
        'megacap_symbols': MEGACAP_SYMBOLS,
        'single_name_symbols': SINGLE_NAME_SYMBOLS,
        'optional_symbols': OPTIONAL_SYMBOLS,
        'unavailable_symbols': unavailable_symbols,
        'case_count': len(cases),
        'series_count': len(series),
        'case_counts_by_symbol': case_counts_by_symbol,
        'case_counts_by_tier': case_counts_by_tier,
        'spy_share': spy_share,
        'index_share': index_share,
        'single_name_share': single_name_share,
        'spy_share_cap': spy_share_cap,
        'spy_cap_dropped': spy_cap_dropped,
        'recoverable_case_count': len(raw_cases),
        'volume_floor': index_floor,
        'volume_floor_index': index_floor,
        'volume_floor_single': single_name_floor,
        'volume_floor_basis': volume_floor_basis,
        'contract_selection': ('near-ATM listed strike nearest the session first-bar underlying '
                               'open that has both a CALL and PUT 1m series; the traded right is '
                               'the side with the higher same-day option volume'),
        'direction_policy': ('volume rule: LONG (buy CALL) when the ATM CALL day-volume >= the ATM '
                             'PUT day-volume, else SHORT (buy PUT). Single names are only considered '
                             'on sessions with a same-day 0DTE expiry; each symbol then has to clear '
                             'its tier volume floor (index vs single-name). No scenario labels are used.'),
        'underlying_source': ('eval-data-v2 klines_1m.parquet where present (window through '
                              '2026-09-11); read-only OpenD K_1M for later sessions'),
        'option_source': 'read-only OpenD 127.0.0.1:11111 K_1M trade history',
        'warm_group_note': ('OpenD option history is quota-accounted per (underlying, expiry) '
                            'group: new strikes inside an already-warm 0DTE group are recoverable, '
                            'but brand-new expiry groups are refused once the 7-day quota is spent. '
                            'Only warm same-day expiry groups are targeted; the resulting OpenD '
                            'retention gaps are recorded in opend_gaps.'),
        'price_note': ('Underlying and option series are 1m trade OHLCV, not NBBO. Live uses '
                       'option bid/ask quotes through the same provider boundary.'),
        'metric_note': ('Custody success is measured on the option contract path/fills only; '
                        'underlying-proxy payoff is forbidden.'),
        'validation_reference': 'custody-eval-2026-09-14',
        'expiry_histogram': _histogram(c['expiry'] for c in cases),
        'opend_gaps': gaps,
        'opend_gap_note': ('Sessions/expiry groups skipped because OpenD no longer retains the '
                           'same-day chain (quota or retention). True-0DTE padding is forbidden, '
                           'so these are gaps, not substituted with DTE>=1 contracts.'),
        'filtered_below_floor': filtered,
        'series': series,
        'cases': cases,
    }
    write_slice_docs(root, cases_doc, manifest)
    _write_checksums(root, series)
    zip_path = zip_tree(root)
    digest = sha256(zip_path)
    (root.parent / (root.name + '.zip.sha256')).write_text('%s  %s\n' % (digest, zip_path.name))
    # In-memory only: writing the zip hash back would invalidate CHECKSUMS.sha256.
    manifest['zip'] = {'path': str(zip_path), 'sha256': digest}
    if logger:
        logger('frozen %d true-0DTE cases / %d series -> %s (zip sha256 %s)' % (
            len(cases), len(series), root, digest))
    return manifest


def _write_checksums(root, series):
    lines = []
    for item in series:
        for rel, digest in (item.get('sha256') or {}).items():
            lines.append('%s  %s' % (digest, item[rel]))
    lines.append('%s  %s' % (sha256(Path(root) / 'cases.json'), 'cases.json'))
    lines.append('%s  %s' % (sha256(Path(root) / 'manifest.json'), 'manifest.json'))
    (Path(root) / 'CHECKSUMS.sha256').write_text('\n'.join(lines) + '\n')


def build_argument_parser():
    import argparse
    parser = argparse.ArgumentParser(
        prog='custody fetch-train-0dte',
        description='Build the formal TRUE-0DTE paired custody train (expiry == trade_date only).')
    parser.add_argument('--out', required=True,
                        help='output directory, e.g. out/custody-train-0dte')
    parser.add_argument('--v2-dir', default=None,
                        help='eval-data-v2 directory for reused underlying 1m; defaults to $CUSTODY_V2_DIR')
    parser.add_argument('--start', default=WINDOW_START)
    parser.add_argument('--end', default=WINDOW_END)
    parser.add_argument('--underlyings', default=','.join(DEFAULT_SYMBOLS),
                        help='comma-separated underlyings in priority order')
    parser.add_argument('--volume-floor', type=float, default=None,
                        help='absolute index-tier contracts/day floor (default: auto from pooled index-ETF median)')
    parser.add_argument('--single-name-floor', type=float, default=None,
                        help='absolute single-name contracts/day floor (default: 10%% of the index floor)')
    parser.add_argument('--spy-share-cap', type=float, default=SPY_SHARE_CAP,
                        help='max SPY share of cases; lowest-volume SPY sessions are held out (default: %g)' % SPY_SHARE_CAP)
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
        manifest = build_0dte_train_slice(
            args.out,
            v2_dir=args.v2_dir,
            start=args.start,
            end=args.end,
            symbols=[s.strip() for s in args.underlyings.split(',') if s.strip()],
            market=market,
            host=args.host,
            port=args.port,
            volume_floor=args.volume_floor,
            single_name_floor=args.single_name_floor,
            spy_share_cap=args.spy_share_cap,
            min_option_bars=args.min_option_bars,
            max_moneyness=args.max_moneyness,
            logger=log,
        )
    finally:
        market.close()
        if handle is not None:
            handle.close()
    print(json.dumps({
        'dataset': manifest['dataset'], 'role': manifest['role'],
        'max_dte': manifest['max_dte'], 'case_count': manifest['case_count'],
        'series_count': manifest['series_count'],
        'sessions': [manifest['sessions'][0], manifest['sessions'][-1]] if manifest['sessions'] else [],
        'underlyings': manifest['underlyings'],
        'volume_floor': manifest['volume_floor'],
        'volume_floor_index': manifest['volume_floor_index'],
        'volume_floor_single': manifest['volume_floor_single'],
        'case_counts_by_tier': manifest['case_counts_by_tier'],
        'spy_share': manifest['spy_share'],
        'single_name_share': manifest['single_name_share'],
        'spy_cap_dropped': len(manifest['spy_cap_dropped']),
        'recoverable_case_count': manifest['recoverable_case_count'],
        'opend_gap_count': len(manifest['opend_gaps']),
        'filtered_below_floor': len(manifest['filtered_below_floor']),
        'zip': manifest.get('zip'),
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
