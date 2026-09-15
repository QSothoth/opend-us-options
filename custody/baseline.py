"""Offline option-OHLCV baseline through the same CustodyService as dryrun.

CSV reader only; no OpenD connection, broker, synthetic prices or stock proxy.
All option fills are explicitly a next-bar-close simulation, not actual fills.
"""
from __future__ import annotations

import argparse
import copy
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, time, timedelta
import hashlib
import json
import re
from pathlib import Path
import statistics
import tempfile

from .marketdata import require_paired_bars
from .models import Contract, ET, OrderUpdate, Quote, Session, instant
from .offline import OfflineMarket, assert_paired_slice
from .opend import parse_option_code
from .pnl import assert_custody_role, custody_case_pnl
from .registry import Registry
from .service import CustodyService, ExecutionPolicy
from .timing import IntradayIndicators

SID = 'custody_trend_1m_v1'
VARIANTS = ('baseline', 'fixed_time', 'fixed_entry_same_exit', 'indicator_entry_hold_to_close')


class VariantRegistry:
    """Counterfactuals receive their own config identity; frozen v1 is unchanged."""
    def __init__(self, variant):
        self.item = Registry().get(SID)
        if variant != 'baseline':
            doc = self.item['config']
            doc['diagnostic_variant'] = variant
            if variant in ('fixed_time','fixed_entry_same_exit'):
                doc['case']['entry']['diagnostic_fixed_entry_at'] = '10:00'
            if variant in ('fixed_time','indicator_entry_hold_to_close'):
                doc['case']['exit'].update(hard_stop_atr=1e12,failure_minutes=0,trail_atr=0)
            rule = {k:doc['case'][k] for k in ('entry','exit')}
            doc['case']['id'] = hashlib.sha256(json.dumps(rule,sort_keys=True).encode()).hexdigest()[:12]
            self.item.update(strategy_id=SID+'__'+variant,case_id=doc['case']['id'],
                             sha256=hashlib.sha256(json.dumps(doc,sort_keys=True).encode()).hexdigest())

    def get(self,name):
        if name != self.item['strategy_id']: raise ValueError('unexpected strategy')
        return copy.deepcopy(self.item)

    def list(self):
        return [{k:v for k,v in self.item.items() if k!='config'}]


def verify_slice(root):
    root = Path(root).resolve()
    manifest, doc = assert_paired_slice(root)
    assert_custody_role(manifest.get('role'))
    checked = 0
    checks = root / 'CHECKSUMS.sha256'
    if checks.exists():
        for line in checks.read_text().splitlines():
            if not line.strip(): continue
            digest, name = line.split(maxsplit=1)
            p = (root / name.lstrip('*')).resolve()
            if not p.is_relative_to(root) or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
                raise ValueError('dataset checksum mismatch: ' + name)
            checked += 1
    else:
        for item in manifest.get('series', []):
            for name, digest in (item.get('sha256') or {}).items():
                # Older releases key the checksum map by format (csv/parquet),
                # newer ones by relative filename. Both bind to actual bytes.
                p = (root / item.get(name,name)).resolve()
                if not p.is_relative_to(root) or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
                    raise ValueError('dataset checksum mismatch: ' + name)
                checked += 1
    if not checked: raise ValueError('frozen dataset has no verifiable checksums')
    seen = set()
    for case in doc['cases']:
        key = (case['contract'], case['trade_date'])
        if key in seen: raise ValueError('duplicate contract/session case')
        seen.add(key)
        underlying, expiry, right = parse_option_code(case['contract'])
        dte = (date.fromisoformat(expiry) - date.fromisoformat(case['trade_date'])).days
        if not 0 <= dte <= 4: raise ValueError('DTE outside 0..4')
        if case['symbol'].removeprefix('US.') != underlying:
            raise ValueError('underlying/contract mismatch')
        if right != ('CALL' if case['direction'] == 'LONG' else 'PUT'):
            raise ValueError('direction/right mismatch')
    return manifest, doc['cases'], checked


def session_for(case, manifest):
    day = date.fromisoformat(case['trade_date'])
    meta = manifest.get('session', {})
    close = instant(meta['close']) if meta.get('close') else datetime.combine(day,time(16),tzinfo=ET)
    opened = instant(meta['open']) if meta.get('open') else datetime.combine(day,time(9,30),tzinfo=ET)
    return Session(day.isoformat(), opened, close)


class CaseCatalog:
    def __init__(self, case): self.case = case
    def resolve(self, code):
        if code != self.case['contract']: raise ValueError('unexpected contract')
        underlying, expiry, right = parse_option_code(code)
        strike = int(re.search(r'[CP](\d+)$',code).group(1))/1000
        if 'strike' in self.case and self.case['strike'] != strike:
            raise ValueError('contract/strike mismatch')
        return Contract(code,underlying,expiry,right,strike,
                        self.case.get('multiplier',100))


class CaseCalendar:
    def __init__(self, session): self.value = session
    def session(self, day):
        if day != self.value.day: raise ValueError('unexpected session')
        return self.value


class NextBarFills:
    """Only fills on an observed option bar AFTER the previously queued intent.

    The displayed intent limit is a reference to the last known close. This
    simulation explicitly models repricing at a later bar close and does not
    claim a historical limit order would have filled at that price.
    """
    def __init__(self, service, job_id, delay_minutes=1):
        if service.mode != 'dryrun': raise ValueError('simulation must be dryrun')
        if type(delay_minutes) is not int or delay_minutes < 1:
            raise ValueError('fill delay must be at least one minute')
        self.service, self.job_id = service, job_id
        self.delay = timedelta(minutes=delay_minutes)
        self.pending = []
        self.seen = set()
        self.fills = []

    def observe(self, option_bar, underlying_mark):
        if option_bar is None or option_bar.volume <= 0 or option_bar.close <= 0:
            return
        now = option_bar.close_time
        for order in list(self.pending):
            if now < instant(order['created_at']) + self.delay: continue
            state = self.service.get_job(self.job_id)
            current = next(o for o in state['orders'] if o['client_order_id'] == order['client_order_id'])
            if current['status'] in ('FILLED', 'CANCELED', 'REJECTED'):
                self.pending.remove(order); continue
            if underlying_mark is None: continue
            event = OrderUpdate(order['client_order_id'], 1, 'FILLED', order['quantity'], now,
                                underlying_mark, option_bar.close,
                                now if order['side'] == 'BUY_OPEN' else None)
            self.service.apply_update(event, now)
            self.fills.append({'side':order['side'],'at':now.isoformat(),'price':option_bar.close,
                               'signal_at':order['created_at'],'decision_at':order.get('decision_at',order['created_at']),'qty':order['quantity'],
                               'basis':'next_option_bar_close_simulated_reprice','reason':order['reason']})
            self.pending.remove(order)

    def queue(self, state):
        for order in state.get('orders',[]):
            if order['kind'] != 'LIMIT' or order['status'] != 'CREATED' or order['client_order_id'] in self.seen:
                continue
            self.seen.add(order['client_order_id']); self.pending.append(order)
            # Internal simulated OPEN acknowledgment only. No broker exists.
            self.service.apply_update(OrderUpdate(order['client_order_id'],0,'OPEN',0,
                                                  instant(order['created_at'])), instant(order['created_at']))


def replay_case(case, underlying, option, session, variant='baseline', delay_minutes=1, strategy_override=None):
    if variant not in VARIANTS: raise ValueError('unknown variant')
    if strategy_override is not None:
        from .adaptive import CandidateRegistry, AdaptiveIndicators
        registry = CandidateRegistry(strategy_override)
        strategy = registry.get(registry.item['strategy_id'])
        indicators = AdaptiveIndicators(strategy,case['symbol'],case['direction'],session)
    else:
        registry = VariantRegistry(variant)
        strategy = registry.get(registry.item['strategy_id'])
        indicators = IntradayIndicators(strategy, case['symbol'], case['direction'], session)
    ub = {b.close_time:b for b in underlying if session.opens < b.close_time < session.closes}
    ob = {b.close_time:b for b in option if session.opens < b.close_time < session.closes}
    # Some options continue trading to 16:15; this product exits before stock close.
    with tempfile.TemporaryDirectory(prefix='custody-ohlcv-') as tmp:
        service = CustodyService(Path(tmp)/'jobs.sqlite','offline-baseline',CaseCatalog(case),
                                 CaseCalendar(session),mode='dryrun',
                                 registry=registry,
                                 policy=ExecutionPolicy(entry_timeout_seconds=24*3600))
        job = service.create_job({'strategy_id':strategy['strategy_id'],'symbol':case['symbol'],'direction':case['direction'],
                                  'contract':case['contract'],'trade_date':case['trade_date'],
                                  'max_qty':case.get('qty',1)},session.opens)
        fills = NextBarFills(service,job['id'],delay_minutes)
        last_underlying = None
        gap_count = 0
        state = service.get_job(job['id'])
        now = session.opens + timedelta(minutes=1)
        while now < session.closes:
            bar, op = ub.get(now), ob.get(now)
            if bar: last_underlying = bar.close
            # Existing intent fills first, then today's newly completed underlying
            # bar can make a new decision. A new order cannot fill on this bar.
            fills.observe(op,last_underlying)
            quote = Quote(case['contract'],op.close,op.close,now) if op and op.close>0 and op.volume>0 else None
            if bar:
                frame = indicators.step(bar)
                if variant in ('fixed_time','fixed_entry_same_exit'):
                    frame = replace(frame,entry_ready=now >= session.opens.replace(hour=10,minute=0),
                                    entry_reason='fixed_10_00',diagnostics=None)
                state = service.on_frame(job['id'],frame,now,quote)
                gap_count += bool(frame.diagnostics and frame.diagnostics['gap_before_bar'])
            else:
                state = service.heartbeat(job['id'],now,quote)
            fills.queue(state)
            if state['state']=='DONE': break
            now += timedelta(minutes=1)
        state = service.get_job(job['id'])
        buys = [f for f in fills.fills if f['side']=='BUY_OPEN']
        sells = [f for f in fills.fills if f['side']=='SELL_CLOSE']
        complete = len(buys)==len(sells)==1 and sells[0]['at']>buys[0]['at'] and state['position_qty']==0
        entry, exit_ = (buys[0] if buys else None), (sells[0] if sells else None)
        block = custody_case_pnl(entry,exit_,case.get('qty',1),state['contract']['multiplier'],case['direction'])
        return {'case':{k:case[k] for k in ('symbol','direction','contract','trade_date')},
                'dte':(date.fromisoformat(state['contract']['expiry'])-date.fromisoformat(case['trade_date'])).days,
                'variant':variant,'strategy_id':strategy['strategy_id'],'strategy_sha256':strategy['sha256'],
                'one_round_trip':complete,'state':state['state'],
                'failure':None if complete else (state['attention'] or 'INCOMPLETE_ROUND_TRIP'),
                'entry':entry,'exit':exit_,'option_pnl':block,'entry_reason':state.get('entry_reason'),
                'exit_reason':state['exit_reason'],'entry_diagnostics':state.get('entry_diagnostics'),
                'holding_minutes':(instant(exit_['at'])-instant(entry['at'])).total_seconds()/60 if complete else None,
                'missing_underlying_intervals':gap_count,'orders':len(state['orders']),
                'orders_never_submitted':True}


def metrics(results, fee_per_side=0.0, slippage_bps_per_side=0.0):
    pnls, returns, fees = [], [], []
    for r in results:
        if not r['one_round_trip']: continue
        p = r['option_pnl']; q,m = p['qty'],p['multiplier']; slip = slippage_bps_per_side / 10000
        paid = p['entry_price']*(1+slip)*q*m
        fee = 2*fee_per_side*q
        pnl = p['exit_price']*(1-slip)*q*m-paid-fee
        pnls.append(pnl); returns.append(pnl/paid); fees.append(fee)
    def stats(values):
        wins,losses = [v for v in values if v>1e-10], [v for v in values if v < -1e-10]
        avg_win = statistics.mean(wins) if wins else None
        avg_loss = abs(statistics.mean(losses)) if losses else None
        return {'mean':statistics.mean(values) if values else None,
                'median':statistics.median(values) if values else None,
                'wins':len(wins),'losses':len(losses),'flat':len(values)-len(wins)-len(losses),
                'win_rate':len(wins)/len(values) if values else None,
                'average_win':avg_win,'average_loss':avg_loss,
                'payoff_ratio':avg_win/avg_loss if avg_win is not None and avg_loss else None,
                'profit_factor':sum(wins)/abs(sum(losses)) if losses else None,
                'best':max(values) if values else None,'worst':min(values) if values else None}
    return {'case_count':len(results),'completed':len(pnls),'failed':len(results)-len(pnls),
            'coverage':len(pnls)/len(results) if results else 0,
            'total_option_pnl':sum(pnls) if len(pnls)==len(results) else None,
            'completed_case_pnl':sum(pnls),'dollar':stats(pnls),'premium_return':stats(returns),
            'fee_per_side':fee_per_side,'slippage_bps_per_side':slippage_bps_per_side,
            'mean_holding_minutes':statistics.mean(r['holding_minutes'] for r in results if r['one_round_trip']) if pnls else None,
            'fallback_count':sum(r['entry_reason']=='deadline_fallback' for r in results)}


def summarize(results):
    groups = {}
    for key in ('trade_date','symbol','direction','dte'):
        grouped = defaultdict(list)
        for r in results: grouped[str(r.get(key,r['case'].get(key)))].append(r)
        groups[key] = {k:metrics(v) for k,v in sorted(grouped.items())}
    return {'primary':metrics(results),'stress':[metrics(results,.65,bps) for bps in (0,25,50,100)],
            'by':groups,'top_winners':sorted([r for r in results if r['one_round_trip']],
                                            key=lambda r:r['option_pnl']['option_return'],reverse=True)[:5]}


def verify_freeze(path):
    if path is None: raise ValueError('validation requires --freeze made before opening its bars')
    doc = json.loads(Path(path).read_text())
    repo = Path(__file__).resolve().parents[1]
    for name, digest in doc['source_sha256'].items():
        if hashlib.sha256((repo/name).read_bytes()).hexdigest() != digest:
            raise ValueError('frozen source changed: '+name)
    return doc


def run(root, out, variants=('baseline',), limit=None, delay_minutes=1, freeze=None):
    manifest,cases,checked = verify_slice(root)
    if manifest['role'] != 'train/custody':
        verify_freeze(freeze)
        if tuple(variants) != ('baseline','fixed_time') or delay_minutes != 1:
            raise ValueError('validation only reports frozen baseline and predeclared fixed-time benchmark')
    if manifest['role'] != 'train/custody' and limit:
        raise ValueError('never select only some validation cases')
    market = OfflineMarket(root,prefer_csv=True)
    report = {'dataset':manifest['dataset'],'role':manifest['role'],'strategy_id':SID,
              'strategy_sha256':Registry().get(SID)['sha256'],'checksums_verified':checked,
              'success_metric':'option_ohlcv_simulated_pnl','real_fills':False,
              'fill_model':'next positive-volume option bar close, simulated reprice; not NBBO/limit-fill evidence',
              'fill_delay_minutes':delay_minutes,'orders_never_submitted':True,'variants':{}}
    chosen = cases[:limit] if limit else cases
    for variant in variants:
        results = []
        for i,case in enumerate(chosen):
            underlying,option = require_paired_bars(market,case['symbol'],case['contract'],case['trade_date'])
            results.append(replay_case(case,underlying,option,session_for(case,manifest),variant,delay_minutes))
            if (i+1)%20==0: print(f'{variant}: {i+1}/{len(chosen)} cases',flush=True)
        report['variants'][variant] = {'summary':summarize(results),'cases':results}
    out = Path(out); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v['summary']['primary'] for k,v in report['variants'].items()},indent=2),flush=True)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--slice',required=True); p.add_argument('--out',required=True)
    p.add_argument('--variants',nargs='+',choices=VARIANTS,default=['baseline'])
    p.add_argument('--limit',type=int); p.add_argument('--delay-minutes',type=int,default=1)
    p.add_argument('--freeze',help='required frozen source manifest for validation')
    args = p.parse_args(argv)
    report = run(args.slice,args.out,args.variants,args.limit,args.delay_minutes,args.freeze)
    return 0 if all(v['summary']['primary']['failed']==0 for v in report['variants'].values()) else 2


if __name__ == '__main__':
    raise SystemExit(main())
