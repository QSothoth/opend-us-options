"""Durable one-round-trip state machine and outbox. Broker I/O is injected."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib, json, sqlite3
from .models import JobRequest, Quote, Frame, OrderUpdate, ET, instant, positive, symbol
from .registry import Registry

TERMINAL = {'FILLED', 'CANCELED', 'REJECTED'}
ACTIVE = {'CREATED', 'DISPATCHING', 'UNKNOWN', 'OPEN', 'PARTIAL'}


@dataclass(frozen=True)
class ExecutionPolicy:
    quote_max_age_seconds: float = 5
    frame_max_age_seconds: float = 15
    entry_timeout_seconds: float = 30
    max_spread_fraction: float = .30

    def __post_init__(self):
        for k,v in asdict(self).items(): positive(v,k)
        if self.max_spread_fraction > 1: raise ValueError('invalid spread fraction')


def encode(x): return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False)


class CustodyService:
    def __init__(self, db_path, account, contracts, calendar, registry=None, policy=None, mode='paper'):
        if not account or mode not in ('paper','live','dryrun'): raise ValueError('account and server mode required')
        if str(db_path) == ':memory:': raise ValueError('durable file database required')
        self.path=str(db_path);self.account=account;self.contracts=contracts;self.calendar=calendar
        self.registry=registry or Registry();self.policy=policy or ExecutionPolicy();self.mode=mode
        self._migrate_contract_scope()
        with self._tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS service_modes (account TEXT PRIMARY KEY, mode TEXT NOT NULL)')
            bound=db.execute('SELECT mode FROM service_modes WHERE account=?',(account,)).fetchone()
            if bound and bound[0]!=mode: raise ValueError('database account already bound to another mode')
            db.execute('INSERT OR IGNORE INTO service_modes VALUES (?,?)',(account,mode))
            db.execute('CREATE TABLE IF NOT EXISTS strategy_versions (id TEXT PRIMARY KEY, sha TEXT NOT NULL)')
            for item in self.registry.list():
                prior=db.execute('SELECT sha FROM strategy_versions WHERE id=?',(item['strategy_id'],)).fetchone()
                if prior and prior[0]!=item['sha256']:raise ValueError('immutable strategy version changed')
                db.execute('INSERT OR IGNORE INTO strategy_versions VALUES (?,?)',(item['strategy_id'],item['sha256']))
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, account TEXT NOT NULL, symbol TEXT NOT NULL, contract TEXT NOT NULL, day TEXT NOT NULL, fingerprint TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(account,contract,day))')
            db.execute('CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS order_events (order_id TEXT NOT NULL, sequence INTEGER NOT NULL, job_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(order_id,sequence))')

    def _migrate_contract_scope(self):
        """Preserve existing job IDs/order FKs when adding contract-level scope."""
        db = sqlite3.connect(self.path, timeout=15)
        try:
            cols = {r[1] for r in db.execute('PRAGMA table_info(jobs)')}
            if not cols or 'contract' in cols:
                return
            bound = db.execute('SELECT mode FROM service_modes WHERE account=?', (self.account,)).fetchone()
            if bound and bound[0] != self.mode:
                raise ValueError('database account already bound to another mode')
            db.execute('PRAGMA foreign_keys=OFF'); db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE jobs_new (id TEXT PRIMARY KEY, account TEXT NOT NULL, symbol TEXT NOT NULL, contract TEXT NOT NULL, day TEXT NOT NULL, fingerprint TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(account,contract,day))')
            for row in db.execute('SELECT id,account,symbol,day,fingerprint,body FROM jobs').fetchall():
                db.execute('INSERT INTO jobs_new VALUES (?,?,?,?,?,?,?)', (*row[:3], json.loads(row[5])['request']['contract'], *row[3:]))
            db.execute('DROP TABLE jobs'); db.execute('ALTER TABLE jobs_new RENAME TO jobs')
            if db.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('job migration foreign-key check failed')
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally:
            db.close()

    @staticmethod
    def _baseline(job):
        return job['strategy'].get('timing_model') == 'intraday_v1'

    @contextmanager
    def _tx(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA journal_mode=WAL');db.execute('BEGIN IMMEDIATE')
        try: yield db;db.commit()
        except BaseException: db.rollback();raise
        finally: db.close()

    def _load(self,db,job_id):
        r=db.execute('SELECT body FROM jobs WHERE id=? AND account=?',(job_id,self.account)).fetchone()
        if r is None: raise KeyError('job not found')
        return json.loads(r[0])

    def _save(self,db,j): db.execute('UPDATE jobs SET body=? WHERE id=?',(encode(j),j['id']))
    def _orders(self,db,j): return [json.loads(r[0]) for r in db.execute('SELECT body FROM orders WHERE job_id=? ORDER BY rowid',(j['id'],))]
    def _store_order(self,db,o): db.execute('UPDATE orders SET body=? WHERE id=?',(encode(o),o['client_order_id']))

    def create_job(self,payload,now):
        now=instant(now);req=JobRequest.parse(payload,now);strategy=self.registry.get(req.strategy_id)
        baseline = strategy.get('timing_model') == 'intraday_v1'
        if baseline and self.mode != 'dryrun':
            raise ValueError('custody baseline is dryrun-only')
        scope, scope_value = ('contract', req.contract) if baseline else ('symbol', req.symbol)
        fingerprint=hashlib.sha256(encode(asdict(req)).encode()).hexdigest()
        # Existing idempotent jobs remain readable after the date/entry deadline.
        with self._tx() as db:
            for prior in db.execute(f'SELECT body FROM jobs WHERE account=? AND {scope}=? AND day!=?',(self.account,scope_value,req.trade_date)):
                if json.loads(prior[0])['state']!='DONE':raise ValueError('previous session job unresolved')
            old=db.execute(f'SELECT id,fingerprint FROM jobs WHERE account=? AND {scope}=? AND day=?',(self.account,scope_value,req.trade_date)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint: raise ValueError('one_job_per_symbol_day: existing request differs')
                return self._load(db,old['id'])
        if now.astimezone(ET).date().isoformat()!=req.trade_date: raise ValueError('active Job date must be today in ET; use replay for historical dates')
        contract=self.contracts.resolve(req.contract);contract.validate(req,same_day_only=self.mode!='dryrun')
        if baseline:
            from datetime import date
            if (date.fromisoformat(contract.expiry) - date.fromisoformat(req.trade_date)).days > 4:
                raise ValueError('baseline contract must have DTE <= 4')
        session=self.calendar.session(req.trade_date)
        if session.day!=req.trade_date: raise ValueError('calendar date mismatch')
        e,x=strategy['config']['case']['entry'],strategy['config']['case']['exit']
        if e['signal_minutes'] not in (1,5) or x['style']!='none' or any(x[k] for k in ('structure_minutes','expanded','stall','late_trail')):
            raise ValueError('strategy requires an unsupported execution timing module')
        midnight=session.opens.astimezone(ET).replace(hour=0,minute=0,second=0,microsecond=0)
        flatten=min(midnight+timedelta(minutes=x['flatten']),session.closes-timedelta(minutes=15))
        deadline=min(midnight+timedelta(minutes=e['entry_deadline']),flatten-timedelta(microseconds=1))
        fallback = deadline
        if baseline:
            deadline = flatten - timedelta(minutes=1)
            fallback = min(midnight + timedelta(minutes=e['entry_deadline']), deadline)
        if now>deadline: raise ValueError('entry window closed')
        job_id=hashlib.sha256(encode([self.account,scope_value,req.trade_date]).encode()).hexdigest()[:32]
        j={'id':job_id,'request':asdict(req),'strategy':strategy,'contract':asdict(contract),'mode':self.mode,'state':'IDLE','position_qty':0,'entry_underlying':None,'entry_at':None,'best':None,'atr':None,'last_bar':None,'exit_requested':False,'exit_reason':None,'attention':None,'opens':session.opens.isoformat(),'flatten_at':flatten.isoformat(),'deadline':deadline.isoformat(),'created_at':now.isoformat()}
        if baseline:
            j.update(fallback_at=fallback.isoformat(), entry_reason=None, entry_atr=None, against_count=0)
        with self._tx() as db:
            for prior in db.execute(f'SELECT body FROM jobs WHERE account=? AND {scope}=? AND day!=?',(self.account,scope_value,req.trade_date)):
                if json.loads(prior[0])['state']!='DONE':raise ValueError('previous session job unresolved')
            old=db.execute(f'SELECT id,fingerprint FROM jobs WHERE account=? AND {scope}=? AND day=?',(self.account,scope_value,req.trade_date)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint: raise ValueError('one_job_per_symbol_day: existing request differs')
                return self._load(db,old['id'])
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',(job_id,self.account,req.symbol,req.contract,req.trade_date,fingerprint,encode(j)))
        return j

    def flag_attention(self,job_id,reason):
        with self._tx() as db:
            j=self._load(db,job_id);j['attention']=reason;self._save(db,j)

    def stop_job(self,job_id,now):
        now=instant(now)
        with self._tx() as db:
            j=self._load(db,job_id)
            if j['state']!='DONE':self._request_exit(db,j,'operator_stop',now,None)
            self._save(db,j)
        return self.get_job(job_id)

    def get_job(self,job_id):
        with self._tx() as db:
            j=self._load(db,job_id);return {**j,'orders':self._orders(db,j)}

    def _quote(self,j,quote,now,allow_wide=False):
        try:
            if not isinstance(quote,Quote) or quote.contract!=j['request']['contract']: return False
            bid=positive(quote.bid,'bid');ask=positive(quote.ask,'ask')
            age=(now-instant(quote.as_of)).total_seconds()
            return 0<=age<=self.policy.quote_max_age_seconds and ask>=bid and (allow_wide or (ask-bid)/ask<=self.policy.max_spread_fraction)
        except (ValueError,TypeError): return False

    def _new(self,db,j,kind,side,qty,now,quote=None,target=None):
        existing=self._orders(db,j);suffix='BUY' if side=='BUY_OPEN' else ('CANCEL_'+target if kind=='CANCEL' else 'SELL_'+str(sum(o['side']=='SELL_CLOSE' for o in existing)+1))
        key=j['id']+':'+suffix
        if any(o['client_order_id']==key for o in existing): return
        o={'client_order_id':key,'job_id':j['id'],'account':self.account,'mode':self.mode,'kind':kind,'side':side,'contract':j['request']['contract'],'quantity':qty,'limit_price':(quote.ask if side=='BUY_OPEN' else quote.bid) if quote else None,'signal_bar_close':j['last_bar'],'reason':j['exit_reason'] or ('entry' if side=='BUY_OPEN' else 'cancel'),'quote_as_of':quote.as_of.isoformat() if quote else None,'target':target,'position_effect':'OPEN' if side=='BUY_OPEN' else 'CLOSE' if side=='SELL_CLOSE' else None,'reduce_only':side=='SELL_CLOSE','status':'CREATED','cumulative_qty':0,'sequence':-1,'last_update':None,'created_at':now.isoformat()}
        if self._baseline(j):
            o['timing_atr'] = j['atr']
            if side == 'BUY_OPEN': o['reason'] = j.get('entry_reason') or 'entry'
        db.execute('INSERT INTO orders VALUES (?,?,?)',(key,j['id'],encode(o)))

    def _exit(self,db,j,now,quote):
        orders=self._orders(db,j);buys=[o for o in orders if o['side']=='BUY_OPEN']
        if buys and buys[0]['status']=='CREATED':
            buys[0]['status']='CANCELED';self._store_order(db,buys[0])
        if buys and buys[0]['status'] not in TERMINAL:
            self._new(db,j,'CANCEL','CANCEL',0,now,target=buys[0]['client_order_id']);j['attention']='WAITING_ENTRY_CANCEL_CONFIRMATION';return
        if j['position_qty']==0:
            j['state']='DONE';j['attention']=None;return
        if any(o['side']=='SELL_CLOSE' and o['status'] in ACTIVE for o in orders): return
        if not self._quote(j,quote,now,allow_wide=True): j['attention']='EXIT_WAITING_VALID_QUOTE';return
        self._new(db,j,'LIMIT','SELL_CLOSE',j['position_qty'],now,quote);j['attention']=None

    def _request_exit(self,db,j,reason,now,quote):
        j['exit_requested']=True;j['exit_reason']=j['exit_reason'] or reason;j['state']='EXIT';self._exit(db,j,now,quote)

    def _clock(self,db,j,now,quote):
        if j['state']=='DONE': return
        orders=self._orders(db,j);buys=[o for o in orders if o['side']=='BUY_OPEN']
        if now>=instant(j['flatten_at']): self._request_exit(db,j,'scheduled_flatten',now,quote);return
        if j['entry_at'] and now>=instant(j['entry_at'])+timedelta(minutes=j['strategy']['config']['case']['exit']['hold']): self._request_exit(db,j,'max_hold',now,quote);return
        if j['exit_requested']: self._exit(db,j,now,quote);return
        if buys and buys[0]['status'] not in TERMINAL and (now-instant(buys[0]['created_at'])).total_seconds()>=self.policy.entry_timeout_seconds:
            if buys[0]['status']=='CREATED':
                buys[0]['status']='CANCELED';self._store_order(db,buys[0]);j['state']='DONE'
            else:
                self._new(db,j,'CANCEL','CANCEL',0,now,target=buys[0]['client_order_id']);j['attention']='ENTRY_TIMEOUT_CANCEL_PENDING'
        if not buys and now>instant(j['deadline']):
            j['state']='DONE'
            if self._baseline(j): j['attention']='ENTRY_NOT_FILLED'

    def heartbeat(self,job_id,now,quote=None,underlying_mark=None,mark_as_of=None):
        now=instant(now)
        with self._tx() as db:
            j=self._load(db,job_id);self._clock(db,j,now,quote)
            if not self._baseline(j) and j['position_qty'] and underlying_mark is not None and mark_as_of is not None:
                age=(now-instant(mark_as_of)).total_seconds();mark=positive(underlying_mark,'underlying_mark')
                sign=1 if j['request']['direction']=='LONG' else -1
                if 0<=age<=self.policy.quote_max_age_seconds and sign*(mark-j['entry_underlying'])<=-j['strategy']['config']['case']['exit']['safety']*j['atr']:
                    self._request_exit(db,j,'underlying_safety',now,quote)
            self._save(db,j)
        return self.get_job(job_id)

    def on_frame(self,job_id,frame,now,quote=None):
        now=instant(now)
        with self._tx() as db:
            j=self._load(db,job_id);e,x=j['strategy']['config']['case']['entry'],j['strategy']['config']['case']['exit'];t=instant(frame.bar_close);opened=instant(j['opens'])
            if symbol(frame.symbol)!=j['request']['symbol'] or frame.strategy_hash!=j['strategy']['sha256'] or frame.minutes!=e['signal_minutes']: raise ValueError('frame/config identity mismatch')
            if type(frame.entry_ready) is not bool or type(frame.minutes) is not int: raise ValueError('invalid frame types')
            elapsed=(t-opened).total_seconds()
            if elapsed<=0 or elapsed%(60*frame.minutes) or t.astimezone(ET).date().isoformat()!=j['request']['trade_date']: raise ValueError('not a native completed-bar boundary')
            if not 0<=(now-t).total_seconds()<=self.policy.frame_max_age_seconds: raise ValueError('future or stale bar')
            close=positive(frame.close,'close');atr=positive(frame.daily_atr,'daily_atr')
            if not self._baseline(j) and j['atr'] is not None and abs(j['atr']-atr)>1e-10: raise ValueError('prior daily ATR changed within session')
            if j['last_bar'] and t<instant(j['last_bar']): raise ValueError('out-of-order bar')
            self._clock(db,j,now,quote)
            if j['state']=='DONE' or (j['last_bar'] and t==instant(j['last_bar'])):
                self._save(db,j);return j
            j['last_bar']=t.isoformat();j['atr']=atr
            if j['state']=='IDLE': j['state']='WATCH'
            if self._baseline(j):
                self._on_baseline_frame(db,j,frame,now,quote)
            elif j['position_qty'] and not j['exit_requested']:
                sign=1 if j['request']['direction']=='LONG' else -1;j['best']=max(j['best'],sign*close)
                gain=j['best']-sign*j['entry_underlying'];profit=sign*(close-j['entry_underlying']);held=(t-instant(j['entry_at'])).total_seconds()/60;reason=None
                if x['soft'] and held>=x['soft_grace'] and gain<x['soft_escape']*atr and profit<=-x['soft']*atr: reason='soft_failure_loss'
                elif x['fail'] and held>=x['fail'] and gain<x['progress']*atr and profit<=0: reason='failed_followthrough'
                elif j['best']-sign*close>=x['trail']*atr: reason='trailing'
                if reason: self._request_exit(db,j,reason,now,quote)
            elif j['state']=='WATCH' and frame.entry_ready and t<=instant(j['deadline']):
                minute=t.astimezone(ET).hour*60+t.astimezone(ET).minute
                if minute>=max(570+e['opening_minutes'],e.get('entry_start_override',0)):
                    if self._quote(j,quote,now): self._new(db,j,'LIMIT','BUY_OPEN',j['request']['max_qty'],now,quote);j['state']='ENTRY';j['attention']=None
                    else: j['attention']='ENTRY_WAITING_VALID_QUOTE'
            self._save(db,j)
        return self.get_job(job_id)

    def _on_baseline_frame(self,db,j,frame,now,quote):
        from .timing import baseline_exit
        if type(frame.trend_against) is not bool:
            raise ValueError('invalid trend flag')
        if j['position_qty'] and not j['exit_requested']:
            reason = baseline_exit(j, frame)
            if reason: self._request_exit(db,j,reason,now,quote)
        elif j['state'] == 'WATCH' and now <= instant(j['deadline']):
            fallback = now >= instant(j['fallback_at'])
            if frame.entry_ready or fallback:
                if self._quote(j,quote,now,allow_wide=fallback):
                    j['entry_reason'] = 'deadline_fallback' if fallback else frame.entry_reason
                    j['entry_diagnostics'] = frame.diagnostics
                    self._new(db,j,'LIMIT','BUY_OPEN',j['request']['max_qty'],now,quote)
                    j['state']='ENTRY'; j['attention']=None
                else:
                    j['attention']='ENTRY_WAITING_VALID_QUOTE'

    def apply_update(self,event,now):
        now=instant(now);at=instant(event.as_of)
        if at>now or type(event.sequence) is not int or event.sequence<0: raise ValueError('invalid order event time/sequence')
        if event.status not in {'OPEN','PARTIAL',*TERMINAL}: raise ValueError('unknown broker order status')
        with self._tx() as db:
            row=db.execute('SELECT body FROM orders WHERE id=?',(event.client_order_id,)).fetchone()
            if row is None: raise KeyError('unknown client order ID')
            o=json.loads(row[0]);j=self._load(db,o['job_id'])
            if o['kind']!='LIMIT': raise ValueError('cancel acknowledgment is not a fill')
            body=asdict(event);body['as_of']=at.isoformat()
            if event.first_fill_at is not None:body['first_fill_at']=instant(event.first_fill_at).isoformat()
            body=encode(body)
            if event.sequence==o['sequence']:
                if body!=o['last_update']: raise ValueError('conflicting duplicate event')
                return j
            if event.sequence<o['sequence']: raise ValueError('out-of-order order event; reconcile')
            if at<instant(o['created_at']): raise ValueError('fill predates order intent')
            qty=event.cumulative_qty
            if type(qty) is not int or not o['cumulative_qty']<=qty<=o['quantity']: raise ValueError('invalid cumulative fill quantity')
            if event.status=='FILLED' and qty!=o['quantity']: raise ValueError('FILLED requires complete quantity')
            if event.status=='REJECTED' and qty: raise ValueError('rejected order cannot carry fills')
            if event.status=='PARTIAL' and not 0<qty<o['quantity']: raise ValueError('invalid partial fill')
            if event.status=='OPEN' and qty: raise ValueError('OPEN cannot carry fills')
            if o['status'] in TERMINAL and (event.status!=o['status'] or qty!=o['cumulative_qty']): raise ValueError('terminal order changed; reconcile broker state')
            delta=qty-o['cumulative_qty']
            if delta:
                positive(event.average_option_price,'average_option_price')
                if o['side']=='BUY_OPEN':
                    if j['entry_at'] is None:
                        first=instant(event.first_fill_at)
                        if not instant(o['created_at'])<=first<=at:raise ValueError('invalid first fill timestamp')
                        j['entry_underlying']=positive(event.underlying_mark,'underlying fill mark');j['entry_at']=first.isoformat();j['best']=(1 if j['request']['direction']=='LONG' else -1)*j['entry_underlying']
                        if self._baseline(j): j['entry_atr']=positive(o['timing_atr'],'entry_atr')
                    j['position_qty']+=delta
                else:
                    if delta>j['position_qty']: raise ValueError('sell fill exceeds owned quantity')
                    j['position_qty']-=delta
            o.update(status=event.status,cumulative_qty=qty,sequence=event.sequence,last_update=body,average_option_price=event.average_option_price);self._store_order(db,o)
            db.execute('INSERT INTO order_events VALUES (?,?,?,?)',(o['client_order_id'],event.sequence,j['id'],body))
            if j['exit_requested']:
                self._exit(db,j,now,None)
            elif o['side']=='BUY_OPEN':
                j['state']='IN' if event.status in TERMINAL and j['position_qty'] else 'DONE' if event.status in TERMINAL else 'ENTRY'
                if event.status in TERMINAL:j['attention']=None
            self._clock(db,j,now,None);self._save(db,j)
        return self.get_job(j['id'])

    def dispatch_next(self,adapter,now):
        """Persist intent before I/O; ambiguous submission is never auto-retried.

        Adapter must implement account/mode, submit(intent), cancel(target,key).
        submit returns an authoritative OrderUpdate. cancel acknowledgment does
        NOT mark the original entry canceled; its terminal update is still required.
        """
        now=instant(now)
        if self.mode=='dryrun': raise ValueError('dryrun mode never dispatches broker orders')
        if adapter.account!=self.account or adapter.mode!=self.mode: raise ValueError('adapter/account/mode mismatch')
        with self._tx() as db:
            candidates=[]
            for row in db.execute('SELECT body FROM orders ORDER BY rowid'):
                o=json.loads(row[0])
                if o['account']==self.account and o['status']=='CREATED':candidates.append(o)
            if not candidates:return None
            o=candidates[0];j=self._load(db,o['job_id'])
            if o['side']=='BUY_OPEN' and (j['exit_requested'] or now>instant(j['deadline']) or not 0<=(now-instant(o['created_at'])).total_seconds()<=self.policy.quote_max_age_seconds):
                o['status']='CANCELED';self._store_order(db,o);j['state']='DONE' if not j['position_qty'] else 'IN';self._save(db,j);return {'not_sent':o['client_order_id']}
            # Recheck quote age for sells too. Expired unsent intents are replaced
            # on the next heartbeat with a fresh price and remaining position.
            if o['kind']=='LIMIT' and not 0<=(now-instant(o['quote_as_of'])).total_seconds()<=self.policy.quote_max_age_seconds:
                o['status']='CANCELED';self._store_order(db,o);return {'not_sent':o['client_order_id']}
            o['status']='DISPATCHING';self._store_order(db,o)
        try:
            if o['kind']=='CANCEL':
                adapter.cancel(o['target'],o['client_order_id'])
                with self._tx() as db:
                    o['status']='ACKED';self._store_order(db,o)
            else:
                event=adapter.submit(json.loads(encode(o)))
                if not isinstance(event,OrderUpdate) or event.client_order_id!=o['client_order_id']:raise ValueError('invalid broker acknowledgment')
                self.apply_update(event,now)
            return {'submitted':o['client_order_id']}
        except Exception as exc:
            with self._tx() as db:
                current=json.loads(db.execute('SELECT body FROM orders WHERE id=?',(o['client_order_id'],)).fetchone()[0])
                if current['status']=='DISPATCHING':current['status']='UNKNOWN';self._store_order(db,current)
                j=self._load(db,o['job_id']);j['attention']='RECONCILE_ORDER_STATUS';self._save(db,j)
            return {'unknown':o['client_order_id'],'error_type':type(exc).__name__}
