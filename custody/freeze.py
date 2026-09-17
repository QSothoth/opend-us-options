"""Daily read-only OpenD freeze of the 0DTE custody dataset (both sides, every day).

OpenD keeps expired weeklies only briefly and its option history quota is scarce,
so the dataset grows only if each session is frozen the same day, after the close::

    python3 -m custody freeze --dataset data/custody-0dte-work              # today, default symbols
    python3 -m custody freeze --dataset data/custody-0dte-work --date 2026-09-16 --symbols US.SPY,US.QQQ

Per (symbol, session) it stores, in the Release layout of :mod:`custody.dataset`:

* the complete regular-session underlying 1m bars and the previous close (daily K);
* the listed same-day strike nearest the first 1m open, and **both** its CALL and
  PUT 1m bars as two cases. Direction is never picked from same-day outcomes
  (volume, return, labels) - that was the V1-V4 bias (docs/DATA.md);
* ``selection = "both_sides_atm_at_open"``, ``prev_close`` and ``session_close``.

Symbols without a same-day expiry that day are skipped and recorded in the manifest.
The same command builds validation sets (``--dataset data/custody-eval-<date> --date <date>
--symbols ...``). Training and validation Releases alike must pass ``custody check``
(every symbol/session has the CALL and the PUT) before publication. The command never
modifies an extracted Release in place: point it at a working copy.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

from .dataset import Dataset, DatasetError, parse_option_code, write_bars, write_checksums
from .marketdata import _num, normalize_daily_rows
from .models import ET
from .opend import DEFAULT_HOST, DEFAULT_PORT, OpenDMarket, OpenDTradingCalendar

DEFAULT_SYMBOLS = ('US.SPY', 'US.QQQ', 'US.IWM', 'US.AAPL', 'US.MSFT', 'US.NVDA', 'US.TSLA',
                   'US.META', 'US.AMZN', 'US.GOOGL', 'US.AMD', 'US.MU', 'US.INTC', 'US.AVGO')
SELECTION = 'both_sides_atm_at_open'


class RateLimiter:
    """At most ``calls`` history requests per ``window`` seconds (OpenD allows 60/30s)."""

    def __init__(self, calls=50, window=30.0, clock=time.monotonic, sleep=time.sleep):
        self.calls, self.window, self.clock, self.sleep = calls, window, clock, sleep
        self._stamps, self._lock = deque(), threading.Lock()

    def acquire(self):
        with self._lock:
            while True:
                now = self.clock()
                while self._stamps and now - self._stamps[0] >= self.window:
                    self._stamps.popleft()
                if len(self._stamps) < self.calls:
                    self._stamps.append(now)
                    return
                self.sleep(self.window - (now - self._stamps[0]) + 0.01)


def _nearest_strike(chain_rows, reference):
    strikes = sorted({_num(r.get('strike_price')) for r in chain_rows if _num(r.get('strike_price'))})
    if not strikes:
        return None
    return min(strikes, key=lambda k: (abs(k - reference), k))


def _code_for(chain_rows, strike, right):
    for row in chain_rows:
        code = str(row.get('code', '')).upper()
        try:
            _, _, parsed_right, parsed_strike = parse_option_code(code)
        except DatasetError:
            continue
        if parsed_right == right and abs(parsed_strike - strike) < 1e-6:
            return code
    return None


ROLES = ('train/custody', 'validation/custody')


def freeze_session(market, calendar, dataset_dir, day, symbols=DEFAULT_SYMBOLS, now=None, limiter=None, log=print,
                   role='train/custody'):
    """Freeze one session into ``dataset_dir``. Returns a summary dict."""
    if role not in ROLES:
        raise ValueError('role must be one of %s' % (ROLES,))
    root = Path(dataset_dir)
    now = now or datetime.now(ET)
    limiter = limiter or RateLimiter()
    session = calendar.session(day)
    if now < session.closes + timedelta(minutes=20):
        raise ValueError('freeze after the session close (+20 min) so every 1m bar is final')
    expected = int((session.closes - session.opens).total_seconds() // 60)
    cases_path, manifest_path = root / 'cases.json', root / 'manifest.json'
    root.mkdir(parents=True, exist_ok=True)
    cases = json.loads(cases_path.read_text())['cases'] if cases_path.exists() else []
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        'dataset': root.name, 'role': role, 'max_dte': 0, 'schema': 'custody-0dte/1',
        'selection_policy': SELECTION, 'sessions': [], 'skipped': []}
    if manifest.get('role') != role:
        raise ValueError('dataset %s already has role %s' % (root, manifest.get('role')))
    known = {(c['contract'], c['trade_date']) for c in cases}
    added, skipped = [], []
    for symbol in symbols:
        limiter.acquire()
        underlying = [b for b in market.history_bars(symbol, 'K_1M', day, day)
                      if session.opens < b.close_time <= session.closes]
        if len(underlying) != expected:
            skipped.append({'symbol': symbol, 'trade_date': day, 'reason': 'incomplete underlying 1m (%d/%d)' % (len(underlying), expected)})
            continue
        chain = market.option_chain(symbol, day)
        strike = _nearest_strike(chain, underlying[0].open)
        codes = {right: _code_for(chain, strike, right) for right in ('CALL', 'PUT')} if strike else {}
        if not codes or None in codes.values():
            skipped.append({'symbol': symbol, 'trade_date': day, 'reason': 'no same-day expiry with both CALL and PUT'})
            continue
        limiter.acquire()
        start = (date.fromisoformat(day) - timedelta(days=10)).isoformat()
        prior = [r for r in normalize_daily_rows(market.request_kline(symbol, 'K_DAY', start, day)) if r['date'] < day]
        options = {}
        for right, code in codes.items():
            limiter.acquire()
            options[right] = [b for b in market.history_bars(code, 'K_1M', day, day) if b.close_time.date().isoformat() == day]
        if not all(options.values()):
            skipped.append({'symbol': symbol, 'trade_date': day, 'reason': 'option 1m history empty'})
            continue
        write_bars(root / 'underlying' / (symbol + '.csv'), underlying)
        for right, code in codes.items():
            write_bars(root / 'option' / (code + '.csv'), options[right])
            if (code, day) in known:
                continue
            cases.append({'symbol': symbol, 'contract': code, 'trade_date': day, 'expiry': day, 'right': right,
                          'direction': 'LONG' if right == 'CALL' else 'SHORT', 'strike': strike,
                          'reference_open': underlying[0].open, 'prev_close': prior[-1]['close'] if prior else None,
                          'session_close': session.closes.isoformat(), 'selection': SELECTION,
                          'call_contract': codes['CALL'], 'put_contract': codes['PUT']})
            added.append(code)
        log('frozen %s %s strike=%s CALL=%s PUT=%s' % (symbol, day, strike, codes['CALL'], codes['PUT']))
    cases.sort(key=lambda c: (c['trade_date'], c['symbol'], c['contract']))
    manifest['sessions'] = sorted(set(manifest.get('sessions', [])) | {c['trade_date'] for c in cases})
    manifest['skipped'] = manifest.get('skipped', []) + skipped
    manifest['case_count'] = len(cases)
    manifest['updated_at'] = now.isoformat()
    cases_path.write_text(json.dumps({'cases': cases}, indent=2) + '\n')
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    files = write_checksums(root)
    Dataset(root)  # re-verify the whole directory after writing
    return {'trade_date': day, 'added_cases': added, 'skipped': skipped, 'files': files,
            'both_sides': Dataset(root).sides_report()}


def build_argument_parser():
    parser = argparse.ArgumentParser(prog='custody freeze', description=__doc__.splitlines()[0])
    parser.add_argument('--dataset', required=True, help='working dataset directory (never an extracted Release)')
    parser.add_argument('--date', default=None, help='ET session date (default: today)')
    parser.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS))
    parser.add_argument('--role', choices=ROLES, default='train/custody',
                        help='train/custody for the growing training set, validation/custody for a held-out set')
    parser.add_argument('--host', default=DEFAULT_HOST)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    return parser


def main(argv=None):
    args = build_argument_parser().parse_args(argv)
    day = args.date or datetime.now(ET).date().isoformat()
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    with OpenDMarket(host=args.host, port=args.port) as market:
        summary = freeze_session(market, OpenDTradingCalendar(market), args.dataset, day, symbols, role=args.role)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0
