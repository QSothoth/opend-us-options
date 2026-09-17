"""Durable single-trade state machine: at most one bought 0DTE option per contract/day.

Broker I/O is injected (:class:`custody.ports.Broker`); market data and strategy
decisions arrive from the caller as :class:`~custody.models.Quote` and
:class:`~custody.models.Frame`. Nothing here opens a network connection.

State path: IDLE -> WATCH -> ENTRY -> IN -> EXIT -> DONE.

Guarantees
----------
* One job per account + option contract + ET trade date: each contract is bought once
  and sold once. The same underlying may have several jobs the same day (for example a
  CALL and a PUT). Identical requests are idempotent; a different request for the same
  contract and day conflicts.
* Only 0DTE contracts (expiry == trade date) and only registered strategies; live
  mode additionally requires strategy status ``accepted`` (docs/STANDARD.md).
* Entry requires a strategy signal; no heartbeat or deadline can force a purchase.
  Unfilled entries may retry on another ENTER frame before flatten; a partial
  entry is still the day's only trade.
* Every intent is persisted before broker I/O. An ambiguous submission becomes
  UNKNOWN and is never blindly resubmitted.
* Exit cancels any live entry remainder first and sells only the owned quantity.
  Flatten does not depend on bars arriving. Missing quotes or unknown order status
  leave the job in EXIT with an attention flag, never a false DONE.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import timedelta
import hashlib
import json
import sqlite3

from .models import ET, JobRequest, OrderUpdate, instant, positive, symbol
from .registry import Registry
from .strategy import ACTIONS, flatten_minute

SCHEMA_VERSION = '4'
TERMINAL = {'FILLED', 'CANCELED', 'REJECTED'}
ACTIVE = {'CREATED', 'DISPATCHING', 'UNKNOWN', 'OPEN', 'PARTIAL'}


@dataclass(frozen=True)
class ExecutionPolicy:
    quote_max_age_seconds: float = 5
    frame_max_age_seconds: float = 15
    entry_timeout_seconds: float = 30
    exit_timeout_seconds: float = 30
    max_spread_fraction: float = .30

    def __post_init__(self):
        for key, value in asdict(self).items():
            positive(value, key)
        if self.max_spread_fraction > 1:
            raise ValueError('invalid spread fraction')


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class CustodyService:
    def __init__(self, db_path, account, contracts, calendar, registry=None, policy=None, mode='paper'):
        if not account or mode not in ('paper', 'live', 'dryrun'):
            raise ValueError('account and server mode (paper|live|dryrun) required')
        if str(db_path) == ':memory:':
            raise ValueError('durable file database required')
        self.path, self.account, self.mode = str(db_path), account, mode
        self.contracts, self.calendar = contracts, calendar
        self.registry = registry or Registry()
        self.policy = policy or ExecutionPolicy()
        with self._tx() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None and 'jobs' in tables:
                raise ValueError('database was created by an older custody runtime; use a new database file')
            if version is not None and version[0] != SCHEMA_VERSION:
                raise ValueError('unsupported custody database schema %s' % version[0])
            db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
            db.execute('CREATE TABLE IF NOT EXISTS service_modes (account TEXT PRIMARY KEY, mode TEXT NOT NULL)')
            bound = db.execute('SELECT mode FROM service_modes WHERE account=?', (account,)).fetchone()
            if bound and bound[0] != mode:
                raise ValueError('database account already bound to another mode')
            db.execute('INSERT OR IGNORE INTO service_modes VALUES (?,?)', (account, mode))
            db.execute('CREATE TABLE IF NOT EXISTS strategy_versions (id TEXT PRIMARY KEY, sha TEXT NOT NULL)')
            for item in self.registry.list():
                prior = db.execute('SELECT sha FROM strategy_versions WHERE id=?', (item['strategy_id'],)).fetchone()
                if prior and prior[0] != item['sha256']:
                    raise ValueError('immutable strategy version changed: ' + item['strategy_id'])
                db.execute('INSERT OR IGNORE INTO strategy_versions VALUES (?,?)', (item['strategy_id'], item['sha256']))
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, account TEXT NOT NULL, contract TEXT NOT NULL, '
                       'day TEXT NOT NULL, fingerprint TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(account, contract, day))')
            db.execute('CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS order_events (order_id TEXT NOT NULL, sequence INTEGER NOT NULL, '
                       'job_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(order_id, sequence))')

    # ------------------------------------------------------------ storage
    @contextmanager
    def _tx(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('BEGIN IMMEDIATE')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _load(self, db, job_id):
        row = db.execute('SELECT body FROM jobs WHERE id=? AND account=?', (job_id, self.account)).fetchone()
        if row is None:
            raise KeyError('job not found')
        return json.loads(row[0])

    def _save(self, db, job):
        db.execute('UPDATE jobs SET body=? WHERE id=?', (encode(job), job['id']))

    def _orders(self, db, job):
        return [json.loads(r[0]) for r in db.execute('SELECT body FROM orders WHERE job_id=? ORDER BY rowid', (job['id'],))]

    def _store_order(self, db, order):
        db.execute('UPDATE orders SET body=? WHERE id=?', (encode(order), order['client_order_id']))

    def get_job(self, job_id):
        with self._tx() as db:
            job = self._load(db, job_id)
            job['orders'] = self._orders(db, job)
            return job

    # ------------------------------------------------------------ jobs
    def create_job(self, payload, now):
        now = instant(now)
        request = JobRequest.parse(payload, now)
        strategy = self.registry.get(request.strategy_id)
        if strategy['status'] == 'retired':
            raise ValueError('strategy is retired')
        if self.mode == 'live' and strategy['status'] != 'accepted':
            raise ValueError('live mode requires a strategy with status accepted (docs/STANDARD.md)')
        fingerprint = hashlib.sha256(encode(asdict(request)).encode()).hexdigest()
        existing = self._existing_job(request, fingerprint)
        if existing is not None:
            return existing
        if now.astimezone(ET).date().isoformat() != request.trade_date:
            raise ValueError('job trade_date must be today in ET; use custody evaluate for history')
        contract = self.contracts.resolve(request.contract)
        contract.validate(request)
        session = self.calendar.session(request.trade_date)
        if session.day != request.trade_date:
            raise ValueError('calendar date mismatch')
        flatten = flatten_minute(strategy['config']['params'], session)
        flatten_at = session.opens + timedelta(minutes=flatten)
        if now >= flatten_at:
            raise ValueError('entry window closed: create the job before %s' % flatten_at.isoformat())
        job_id = hashlib.sha256(encode([self.account, request.contract, request.trade_date]).encode()).hexdigest()[:32]
        job = {'id': job_id, 'request': asdict(request), 'strategy': strategy, 'contract': asdict(contract),
               'mode': self.mode, 'state': 'IDLE', 'position_qty': 0, 'entry_at': None, 'entry_underlying': None,
               'entry_reason': None, 'entry_diagnostics': None, 'exit_requested': False, 'exit_reason': None,
               'exit_decision_at': None, 'attention': None, 'last_bar': None,
               'opens': session.opens.isoformat(), 'closes': session.closes.isoformat(),
               'flatten_at': flatten_at.isoformat(),
               'created_at': now.isoformat()}
        with self._tx() as db:
            row = db.execute('SELECT id, fingerprint FROM jobs WHERE account=? AND contract=? AND day=?',
                             (self.account, request.contract, request.trade_date)).fetchone()
            if row is None:
                db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',
                           (job_id, self.account, request.contract, request.trade_date, fingerprint, encode(job)))
            elif row['fingerprint'] != fingerprint:
                raise ValueError('one_job_per_contract_day: a different request already exists for this contract')
        return self.get_job(job_id)

    def _existing_job(self, request, fingerprint):
        with self._tx() as db:
            row = db.execute('SELECT id, fingerprint FROM jobs WHERE account=? AND contract=? AND day=?',
                             (self.account, request.contract, request.trade_date)).fetchone()
            if row is None:
                return None
            if row['fingerprint'] != fingerprint:
                raise ValueError('one_job_per_contract_day: a different request already exists for this contract')
        return self.get_job(row['id'])

    def stop_job(self, job_id, now):
        """Operator stop: cancel the entry or liquidate; never pretends a position is closed."""
        now = instant(now)
        with self._tx() as db:
            job = self._load(db, job_id)
            if job['state'] != 'DONE':
                self._request_exit(db, job, 'operator_stop', now, None)
            self._save(db, job)
        return self.get_job(job_id)

    def flag_attention(self, job_id, attention):
        with self._tx() as db:
            job = self._load(db, job_id)
            job['attention'] = attention
            self._save(db, job)

    # ------------------------------------------------------------ events
    def heartbeat(self, job_id, now, quote=None):
        now = instant(now)
        with self._tx() as db:
            job = self._load(db, job_id)
            self._clock(db, job, now, quote)
            self._save(db, job)
        return self.get_job(job_id)

    def on_frame(self, job_id, frame, now, quote=None):
        now = instant(now)
        with self._tx() as db:
            job = self._load(db, job_id)
            if symbol(frame.symbol) != job['request']['symbol'] or frame.strategy_hash != job['strategy']['sha256']:
                raise ValueError('frame/job identity mismatch')
            if frame.action not in ACTIONS:
                raise ValueError('unknown frame action')
            bar = instant(frame.bar_close)
            elapsed = (bar - instant(job['opens'])).total_seconds()
            if elapsed <= 0 or elapsed % 60 or bar > instant(job['closes']):
                raise ValueError('frame is not a completed regular-session 1m bar')
            if not 0 <= (now - bar).total_seconds() <= self.policy.frame_max_age_seconds:
                raise ValueError('future or stale frame')
            if job['last_bar'] and bar < instant(job['last_bar']):
                raise ValueError('out-of-order frame')
            self._clock(db, job, now, quote)
            fresh = job['state'] != 'DONE' and not (job['last_bar'] and bar == instant(job['last_bar']))
            if fresh:
                job['last_bar'] = bar.isoformat()
                if job['state'] == 'IDLE':
                    job['state'] = 'WATCH'
                if job['state'] == 'WATCH' and frame.action == 'ENTER':
                    self._enter(db, job, now, quote, frame.reason or 'strategy_entry', frame.diagnostics)
                elif job['position_qty'] and not job['exit_requested'] and frame.action == 'EXIT':
                    self._request_exit(db, job, frame.reason or 'strategy_exit', now, quote)
            self._save(db, job)
        return self.get_job(job_id)

    def apply_update(self, event, now):
        now, at = instant(now), instant(event.as_of)
        if at > now or type(event.sequence) is not int or event.sequence < 0:
            raise ValueError('invalid order event time/sequence')
        if event.status not in {'OPEN', 'PARTIAL', *TERMINAL}:
            raise ValueError('unknown broker order status')
        with self._tx() as db:
            row = db.execute('SELECT body FROM orders WHERE id=?', (event.client_order_id,)).fetchone()
            if row is None:
                raise KeyError('unknown client order ID')
            order = json.loads(row[0])
            job = self._load(db, order['job_id'])
            if order['kind'] != 'LIMIT':
                raise ValueError('cancel acknowledgment is not a fill')
            body = asdict(event)
            body['as_of'] = at.isoformat()
            if event.first_fill_at is not None:
                body['first_fill_at'] = instant(event.first_fill_at).isoformat()
            body = encode(body)
            if event.sequence == order['sequence']:
                if body != order['last_update']:
                    raise ValueError('conflicting duplicate event')
                duplicate = True
            elif event.sequence < order['sequence']:
                raise ValueError('out-of-order order event; reconcile')
            else:
                duplicate = False
            if not duplicate:
                self._apply_fill(db, job, order, event, at, body, now)
        return self.get_job(job['id'])

    def _apply_fill(self, db, job, order, event, at, body, now):
        if at < instant(order['created_at']):
            raise ValueError('fill predates order intent')
        qty = event.cumulative_qty
        if type(qty) is not int or not order['cumulative_qty'] <= qty <= order['quantity']:
            raise ValueError('invalid cumulative fill quantity')
        if event.status == 'FILLED' and qty != order['quantity']:
            raise ValueError('FILLED requires complete quantity')
        if event.status == 'REJECTED' and qty:
            raise ValueError('rejected order cannot carry fills')
        if event.status == 'PARTIAL' and not 0 < qty < order['quantity']:
            raise ValueError('invalid partial fill')
        if event.status == 'OPEN' and qty:
            raise ValueError('OPEN cannot carry fills')
        if order['status'] in TERMINAL and (event.status != order['status'] or qty != order['cumulative_qty']):
            raise ValueError('terminal order changed; reconcile broker state')
        delta = qty - order['cumulative_qty']
        if delta:
            positive(event.average_option_price, 'average_option_price')
            if order['side'] == 'BUY_OPEN':
                if job['entry_at'] is None:
                    first = instant(event.first_fill_at)
                    if not instant(order['created_at']) <= first <= at:
                        raise ValueError('invalid first fill timestamp')
                    job['entry_underlying'] = positive(event.underlying_mark, 'underlying fill mark')
                    job['entry_at'] = first.isoformat()
                job['position_qty'] += delta
            else:
                if delta > job['position_qty']:
                    raise ValueError('sell fill exceeds owned quantity')
                job['position_qty'] -= delta
        order.update(status=event.status, cumulative_qty=qty, sequence=event.sequence, last_update=body,
                     average_option_price=event.average_option_price)
        self._store_order(db, order)
        db.execute('INSERT INTO order_events VALUES (?,?,?,?)', (order['client_order_id'], event.sequence, job['id'], body))
        if job['exit_requested']:
            self._exit(db, job, now, None)
        elif order['side'] == 'BUY_OPEN':
            if event.status in TERMINAL:
                self._entry_finished(job, now)
            else:
                job['state'] = 'ENTRY'
        self._clock(db, job, now, None)
        self._save(db, job)

    def dispatch_next(self, adapter, now):
        """Send the oldest CREATED intent. Dryrun never dispatches."""
        now = instant(now)
        if self.mode == 'dryrun':
            raise ValueError('dryrun mode never dispatches broker orders')
        if adapter.account != self.account or adapter.mode != self.mode:
            raise ValueError('adapter/account/mode mismatch')
        with self._tx() as db:
            candidates = [json.loads(r[0]) for r in db.execute('SELECT body FROM orders ORDER BY rowid')]
            candidates = [o for o in candidates if o['account'] == self.account and o['status'] == 'CREATED']
            if not candidates:
                return None
            order = candidates[0]
            job = self._load(db, order['job_id'])
            stale = order['kind'] == 'LIMIT' and not 0 <= (now - instant(order['quote_as_of'])).total_seconds() <= self.policy.quote_max_age_seconds
            if order['side'] == 'BUY_OPEN' and (job['exit_requested'] or now >= instant(job['flatten_at'])):
                stale = True
            if stale:
                # Never send an old price. The next heartbeat/frame re-creates a fresh intent.
                order['status'] = 'CANCELED'
                self._store_order(db, order)
                if order['side'] == 'BUY_OPEN' and not job['exit_requested']:
                    self._entry_finished(job, now)
                self._save(db, job)
                return {'not_sent': order['client_order_id']}
            order['status'] = 'DISPATCHING'
            self._store_order(db, order)
        try:
            if order['kind'] == 'CANCEL':
                adapter.cancel(order['target'], order['client_order_id'])
                with self._tx() as db:
                    order['status'] = 'ACKED'
                    self._store_order(db, order)
            else:
                event = adapter.submit(json.loads(encode(order)))
                if not isinstance(event, OrderUpdate) or event.client_order_id != order['client_order_id']:
                    raise ValueError('invalid broker acknowledgment')
                self.apply_update(event, now)
            return {'submitted': order['client_order_id']}
        except Exception as exc:
            with self._tx() as db:
                current = json.loads(db.execute('SELECT body FROM orders WHERE id=?', (order['client_order_id'],)).fetchone()[0])
                if current['status'] == 'DISPATCHING':
                    current['status'] = 'UNKNOWN'
                    self._store_order(db, current)
                job = self._load(db, order['job_id'])
                job['attention'] = 'RECONCILE_ORDER_STATUS'
                self._save(db, job)
            return {'unknown': order['client_order_id'], 'error_type': type(exc).__name__}

    # ------------------------------------------------------------ rules
    def _quote_ok(self, job, quote, now, allow_wide=False):
        try:
            if quote is None or quote.contract != job['request']['contract']:
                return False
            bid, ask = positive(quote.bid, 'bid'), positive(quote.ask, 'ask')
            age = (now - instant(quote.as_of)).total_seconds()
            return (0 <= age <= self.policy.quote_max_age_seconds and ask >= bid
                    and (allow_wide or (ask - bid) / ask <= self.policy.max_spread_fraction))
        except (ValueError, TypeError):
            return False

    def _new(self, db, job, kind, side, qty, now, quote=None, target=None, reason=None):
        existing = self._orders(db, job)
        if kind == 'CANCEL':
            suffix = 'CANCEL_' + target.rsplit(':', 1)[-1]
        else:
            prefix = 'BUY' if side == 'BUY_OPEN' else 'SELL'
            suffix = '%s_%d' % (prefix, 1 + sum(o['side'] == side for o in existing))
        key = job['id'] + ':' + suffix
        if any(o['client_order_id'] == key for o in existing):
            return None
        order = {'client_order_id': key, 'job_id': job['id'], 'account': self.account, 'mode': self.mode,
                 'kind': kind, 'side': side, 'contract': job['request']['contract'], 'quantity': qty,
                 'limit_price': (quote.ask if side == 'BUY_OPEN' else quote.bid) if quote else None,
                 'quote_as_of': quote.as_of.isoformat() if quote else None, 'target': target, 'reason': reason,
                 'position_effect': {'BUY_OPEN': 'OPEN', 'SELL_CLOSE': 'CLOSE'}.get(side),
                 'reduce_only': side == 'SELL_CLOSE', 'decision_bar': job['last_bar'], 'status': 'CREATED',
                 'cumulative_qty': 0, 'sequence': -1, 'last_update': None, 'created_at': now.isoformat()}
        db.execute('INSERT INTO orders VALUES (?,?,?)', (key, job['id'], encode(order)))
        return order

    def _enter(self, db, job, now, quote, reason, diagnostics):
        if any(o['side'] == 'BUY_OPEN' and o['status'] in ACTIVE for o in self._orders(db, job)):
            return
        job.update(entry_reason=reason, entry_diagnostics=diagnostics)
        if not self._quote_ok(job, quote, now):
            job['attention'] = 'ENTRY_WAITING_VALID_QUOTE'
            return
        self._new(db, job, 'LIMIT', 'BUY_OPEN', job['request']['max_qty'], now, quote, reason=reason)
        job.update(state='ENTRY', entry_reason=reason, entry_diagnostics=diagnostics, attention=None)

    def _entry_finished(self, job, now):
        """A BUY order reached a terminal state (or was dropped unsent)."""
        if job['position_qty']:
            job.update(state='IN', attention=None)
        elif now < instant(job['flatten_at']):
            job.update(state='WATCH', attention='ENTRY_RETRY_PENDING')
        else:
            job.update(state='DONE', attention='ENTRY_NOT_FILLED')

    def _request_exit(self, db, job, reason, now, quote):
        job['exit_decision_at'] = job['exit_decision_at'] or now.isoformat()
        job['exit_requested'] = True
        job['exit_reason'] = job['exit_reason'] or reason
        job['state'] = 'EXIT'
        self._exit(db, job, now, quote)

    def _exit(self, db, job, now, quote):
        orders = self._orders(db, job)
        waiting = False
        for buy in (o for o in orders if o['side'] == 'BUY_OPEN' and o['status'] in ACTIVE):
            if buy['status'] == 'CREATED':
                buy['status'] = 'CANCELED'
                self._store_order(db, buy)
            else:
                self._new(db, job, 'CANCEL', 'CANCEL', 0, now, target=buy['client_order_id'])
                waiting = True
        if waiting:
            job['attention'] = 'WAITING_ENTRY_CANCEL_CONFIRMATION'
            return
        if job['position_qty'] == 0:
            never_entered = job['entry_at'] is None and job['exit_reason'] != 'operator_stop'
            attempted = job['entry_reason'] is not None or any(o['side'] == 'BUY_OPEN' for o in orders)
            outcome = 'ENTRY_NOT_FILLED' if attempted else 'NO_ENTRY_SIGNAL'
            job.update(state='DONE', attention=outcome if never_entered else None)
            return
        sells = [o for o in orders if o['side'] == 'SELL_CLOSE' and o['status'] in ACTIVE]
        if sells:
            sell = sells[-1]
            age = (now - instant(sell['created_at'])).total_seconds()
            if sell['status'] != 'CREATED' and age >= self.policy.exit_timeout_seconds:
                if self._new(db, job, 'CANCEL', 'CANCEL', 0, now, target=sell['client_order_id']):
                    job['attention'] = 'EXIT_REPRICE_CANCEL_PENDING'
            return
        if not self._quote_ok(job, quote, now, allow_wide=True):
            job['attention'] = 'EXIT_WAITING_VALID_QUOTE'
            return
        self._new(db, job, 'LIMIT', 'SELL_CLOSE', job['position_qty'], now, quote, reason=job['exit_reason'])
        job['attention'] = None

    def _clock(self, db, job, now, quote):
        if job['state'] == 'DONE':
            return
        if now >= instant(job['flatten_at']) and not job['exit_requested']:
            self._request_exit(db, job, 'scheduled_flatten', now, quote)
            return
        if job['exit_requested']:
            self._exit(db, job, now, quote)
            return
        buys = [o for o in self._orders(db, job) if o['side'] == 'BUY_OPEN' and o['status'] in ACTIVE]
        for buy in buys:
            if (now - instant(buy['created_at'])).total_seconds() < self.policy.entry_timeout_seconds:
                continue
            if buy['status'] == 'CREATED':
                buy['status'] = 'CANCELED'
                self._store_order(db, buy)
                self._entry_finished(job, now)
            elif self._new(db, job, 'CANCEL', 'CANCEL', 0, now, target=buy['client_order_id']):
                job['attention'] = 'ENTRY_TIMEOUT_CANCEL_PENDING'
