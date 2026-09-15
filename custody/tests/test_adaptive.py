"""Synthetic invariants and service/fast replay agreement, no holdout fixture."""
import copy
import math
import tempfile
import unittest
from dataclasses import asdict,replace
from pathlib import Path
from datetime import timedelta

from custody.adaptive import (AdaptiveIndicators,CandidateRegistry,make_strategy,
    DEFAULT_ENTRY,DEFAULT_EXIT,ENTRY_KEYS,EXIT_KEYS,entry_rule,exit_rule,validate_vectors)
from custody.baseline import replay_case,CaseCatalog,CaseCalendar
from custody.marketdata import Bar
from custody.models import Frame,Quote,OrderUpdate
from custody.service import CustodyService
from custody.dryrun import SameDayHistorySource
from custody.signals import SignalProvider
from custody.tests.test_baseline import OPEN,SESSION,CASE,DAY


def tape(code,option=False):
    rows=[];last=1.0 if option else 100.
    for i in range(1,391):
        close=(1+.3*math.sin(i/40)+.002*i if option else 100+.6*math.sin(i/18)+.008*i)
        volume=(0 if option and i%5 in (0,1) else 100+i%31)
        rows.append(Bar(code,OPEN+timedelta(minutes=i),last,max(last,close)+.01,min(last,close)-.01,close,volume))
        last=close
    return rows


class AdaptiveTests(unittest.TestCase):
    def test_correct_vwap_reclaim(self):
        eng=AdaptiveIndicators(make_strategy(),'SPY','LONG',SESSION)
        fs=[]
        for i,values in enumerate([(100,100,100,100,1),(101,101,101,101,1),(103,105,100,104,100)],1):
            fs.append(eng.step(Bar('US.SPY',OPEN+timedelta(minutes=i),*values)))
        self.assertEqual(fs[-1].diagnostics['vector'][11],0)

    def test_missing_opening_prefix_rejected(self):
        eng=AdaptiveIndicators(make_strategy(),'SPY','LONG',SESSION)
        with self.assertRaisesRegex(ValueError,'prefix'):eng.step(tape('US.SPY')[30])

    def test_partial_nonempty_history_is_filled(self):
        class Market:
            called=False
            def current_bars(self,*a,**k):return tape('US.SPY')[:90][-20:]
            def history_bars(self,*a,**k):self.called=True;return tape('US.SPY')[:90]
        m=Market();rows,_,_=SameDayHistorySource(m,CaseCalendar(SESSION),'US.SPY').collect(OPEN+timedelta(minutes=90))
        self.assertTrue(m.called);self.assertEqual(len(rows),90)

    def test_future_suffix_and_provider_parity(self):
        strategy=make_strategy();rows=tape('US.SPY')[:70]
        eng=AdaptiveIndicators(strategy,'SPY','LONG',SESSION)
        frames=[eng.step(b) for b in rows]
        changed=rows[:40]+[replace(b,open=500,high=601,low=499,close=600,volume=99999) for b in rows[40:]]
        eng2=AdaptiveIndicators(strategy,'SPY','LONG',SESSION)
        self.assertEqual([asdict(eng2.step(b)) for b in changed[:40]],[asdict(f) for f in frames[:40]])
        actual=SignalProvider(CandidateRegistry(strategy)).evaluate(strategy['strategy_id'],'SPY','LONG',rows[-1].close_time,[b.to_record() for b in rows],[],{DAY:SESSION.closes.isoformat()})
        self.assertEqual(asdict(actual),asdict(frames[-1]))

    def test_rejected_entry_retries_without_a_second_position(self):
        strategy=make_strategy();t=OPEN+timedelta(minutes=60)
        with tempfile.TemporaryDirectory() as tmp:
            svc=CustodyService(Path(tmp)/'db','test',CaseCatalog(CASE),CaseCalendar(SESSION),registry=CandidateRegistry(strategy),mode='dryrun')
            j=svc.create_job({**{k:CASE[k] for k in ('symbol','direction','contract','trade_date')},'strategy_id':strategy['strategy_id']},OPEN)
            f=Frame('SPY',strategy['sha256'],t,1,100,.1,False,diagnostics={})
            st=svc.on_frame(j['id'],f,t,Quote(CASE['contract'],1,1,t));o=st['orders'][0]
            st=svc.apply_update(OrderUpdate(o['client_order_id'],0,'REJECTED',0,t),t)
            self.assertEqual(st['state'],'WATCH')
            t2=t+timedelta(minutes=1)
            st=svc.on_frame(j['id'],replace(f,bar_close=t2),t2,Quote(CASE['contract'],1,1,t2))
            self.assertEqual(len(st['orders']),2);self.assertNotEqual(st['orders'][0]['client_order_id'],st['orders'][1]['client_order_id'])
            self.assertEqual(st['position_qty'],0)

    def test_ratchet_never_moves_against_position(self):
        x=list(DEFAULT_EXIT);x[4]=0;x[8]=1;x[9]=2;x[20]=1
        state=[100,0,0,-1e100];lines=[]
        for t,p,atr in [(1,102,.1),(2,103,.1),(3,104,2)]:
            f=[0.]*31;f[0]=t;f[1]=p;f[2]=atr
            exit_rule(f,x,state,100,0,1,0);lines.append(state[3])
        self.assertEqual(lines,sorted(lines))

    def test_no_fixed_profit_target(self):
        state=[100,0,0,-1e100]
        for t in range(1,101):
            f=[0.]*31;f[0]=t;f[1]=100+t;f[2]=1;f[3]=f[4]=f[12]=1
            self.assertEqual(exit_rule(f,DEFAULT_EXIT,state,100,0,1,0),0)

    def test_invalid_parameters_and_live_rejected(self):
        e=list(DEFAULT_ENTRY);e[2]=400
        with self.assertRaises(ValueError):make_strategy(e=e)
        with tempfile.TemporaryDirectory() as tmp:
            strategy=make_strategy();svc=CustodyService(Path(tmp)/'db','test',CaseCatalog(CASE),CaseCalendar(SESSION),registry=CandidateRegistry(strategy),mode='live')
            with self.assertRaisesRegex(ValueError,'dryrun'):
                svc.create_job({**{k:CASE[k] for k in ('symbol','direction','contract','trade_date')},'strategy_id':strategy['strategy_id']},OPEN)

    def test_fast_and_service_sparse_fills_agree(self):
        import numpy as np
        from research.custody_v2.search import evaluate
        for mode,direction,delay in [(0,'LONG',1),(1,'SHORT',2),(3,'LONG',3),(4,'SHORT',1),(6,'LONG',2),(8,'SHORT',3),(9,'LONG',1),(10,'SHORT',2)]:
            case=dict(CASE,direction=direction,contract='US.SPY260911'+('C' if direction=='LONG' else 'P')+'100000')
            e=list(DEFAULT_ENTRY);e[0]=mode;e[12]=2
            x=list(DEFAULT_EXIT);x[1]=1;x[3]=2;x[8]=2;x[9]=4;x[12]=10;x[14]=20
            strategy=make_strategy(1,e,x);ub=tape('US.SPY');ob=tape(case['contract'],True)
            fs=np.full((1,391,31),np.nan);prices=np.zeros((1,391));nb=np.full((1,391),390,dtype=np.int64)
            engine=AdaptiveIndicators(strategy,'SPY',direction,SESSION)
            for b in ub[:-1]:
                f=engine.step(b);fs[0,int(f.diagnostics['vector'][0])]=f.diagnostics['vector']
            for b in ob[:-1]:
                t=int((b.close_time-OPEN).total_seconds()/60)
                if b.volume>0:prices[0,t]=b.close
            last=390
            for t in range(390,-1,-1):
                if prices[0,t]>0:last=t
                nb[0,t]=last
            fast=evaluate(fs,prices,nb,np.array([0]),np.array(e,float),np.array(x,float),delay)[0]
            actual=replay_case(case,ub,ob,SESSION,delay_minutes=delay,strategy_override=strategy)
            self.assertTrue(actual['one_round_trip'])
            for index,side,field in [(0,'entry','signal_at'),(1,'entry','at'),(3,'exit','signal_at'),(4,'exit','at')]:
                expected=(__import__('datetime').datetime.fromisoformat(actual[side][field])-OPEN).total_seconds()/60
                self.assertEqual(fast[index],expected,(mode,direction,delay,actual))
            self.assertAlmostEqual(fast[5],actual['entry']['price']);self.assertAlmostEqual(fast[6],actual['exit']['price'])

if __name__=='__main__':unittest.main()
