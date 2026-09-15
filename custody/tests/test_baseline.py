"""Causal and execution-contract tests. Synthetic bars test mechanics only.

No validation fixture, historical return assertion or network access is used.
"""
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from custody.baseline import (CaseCalendar, CaseCatalog, NextBarFills, SID,
                             metrics, replay_case, verify_freeze, verify_slice)
from custody.dryrun import SameDayHistorySource
from custody.marketdata import Bar
from custody.models import ET, Frame, Quote, Session
from custody.registry import Registry
from custody.service import CustodyService
from custody.signals import SignalProvider
from custody.timing import IntradayIndicators, baseline_exit

DAY = '2026-09-11'
OPEN = datetime(2026,9,11,9,30,tzinfo=ET)
SESSION = Session(DAY,OPEN,OPEN.replace(hour=16,minute=0))
CASE = {'symbol':'US.SPY','direction':'LONG','contract':'US.SPY260911C100000',
        'trade_date':DAY,'strike':100}


def bars(code, slope=0, count=390):
    initial = 100 if code=='US.SPY' else 1
    return [Bar(code,OPEN+timedelta(minutes=i),initial+slope*(i-1),
                initial+slope*i+.01,initial+slope*(i-1)-.01,initial+slope*i,100)
            for i in range(1,count+1)]


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.strategy = Registry().get(SID)

    def tearDown(self): self.tmp.cleanup()

    def service(self, case=CASE):
        s = CustodyService(Path(self.tmp.name)/'state.db','unit',CaseCatalog(case),
                           CaseCalendar(SESSION),mode='dryrun')
        j = s.create_job({**{k:case[k] for k in ('symbol','contract','direction','trade_date')},
                          'strategy_id':SID},OPEN)
        return s,j

    def frame(self, t, ready=False):
        return Frame('SPY',self.strategy['sha256'],t,1,100,.1,ready)

    def test_flat_day_still_completes_one_trade(self):
        r = replay_case(CASE,bars('US.SPY'),bars(CASE['contract']),SESSION)
        self.assertTrue(r['one_round_trip'])
        self.assertEqual(r['entry_reason'],'deadline_fallback')
        self.assertEqual(r['entry']['signal_at'],OPEN.replace(hour=10,minute=30).isoformat())
        self.assertEqual(r['entry']['at'],OPEN.replace(hour=10,minute=31).isoformat())
        self.assertEqual(r['exit']['at'],OPEN.replace(hour=15,minute=46).isoformat())
        self.assertEqual(r['orders'],2)

    def test_call_and_put_both_buy_owned_option(self):
        case = dict(CASE,direction='SHORT',contract='US.SPY260911P100000')
        r = replay_case(case,bars('US.SPY'),bars(case['contract'],slope=.001),SESSION)
        self.assertTrue(r['one_round_trip']); self.assertGreater(r['option_pnl']['option_pnl'],0)
        self.assertEqual(r['entry']['side'],'BUY_OPEN'); self.assertEqual(r['exit']['side'],'SELL_CLOSE')

    def test_future_suffix_cannot_change_prefix_frames(self):
        original = bars('US.SPY',slope=.01,count=90)
        changed = original[:45]+[replace(b,open=500,high=1000,low=1,close=900,volume=999999) for b in original[45:]]
        def evaluate(rows):
            engine=IntradayIndicators(self.strategy,'SPY','LONG',SESSION)
            return [asdict(engine.step(b)) for b in rows]
        self.assertEqual(evaluate(original)[:45],evaluate(changed)[:45])

    def test_provider_and_incremental_frames_agree(self):
        rows = bars('US.SPY',slope=.01,count=35)
        engine = IntradayIndicators(self.strategy,'SPY','LONG',SESSION)
        for bar in rows: expected = engine.step(bar)
        frame = SignalProvider().evaluate(SID,'SPY','LONG',rows[-1].close_time,
                  [b.to_record() for b in rows],[],{DAY:SESSION.closes.isoformat()})
        self.assertEqual(asdict(frame),asdict(expected))

    def test_provider_rejects_future_and_previous_day_bars(self):
        rows = bars('US.SPY',count=30); provider=SignalProvider()
        with self.assertRaisesRegex(ValueError,'future'):
            provider.evaluate(SID,'SPY','LONG',rows[-2].close_time,[b.to_record() for b in rows],[],{DAY:SESSION.closes.isoformat()})
        rows[0]=replace(rows[0],close_time=rows[0].close_time-timedelta(days=1))
        with self.assertRaisesRegex(ValueError,'current-session'):
            provider.evaluate(SID,'SPY','LONG',rows[-1].close_time,[b.to_record() for b in rows],[],{DAY:SESSION.closes.isoformat()})

    def test_price_scale_changes_only_option_dollars(self):
        u=bars('US.SPY'); o=bars(CASE['contract'],slope=.001)
        a=replay_case(CASE,u,o,SESSION)
        b=replay_case(CASE,u,[replace(x,open=x.open*3,high=x.high*3,low=x.low*3,close=x.close*3) for x in o],SESSION)
        self.assertEqual(a['entry']['at'],b['entry']['at']); self.assertEqual(a['exit']['at'],b['exit']['at'])
        self.assertAlmostEqual(b['option_pnl']['option_pnl'],a['option_pnl']['option_pnl']*3)
        self.assertAlmostEqual(b['option_pnl']['option_return'],a['option_pnl']['option_return'])

    def test_fill_requires_later_positive_volume_bar(self):
        svc,job=self.service(); t=OPEN+timedelta(minutes=30)
        state=svc.on_frame(job['id'],self.frame(t,True),t,Quote(CASE['contract'],1,1,t))
        fills=NextBarFills(svc,job['id']); fills.queue(state)
        bar=Bar(CASE['contract'],t,1,1,1,1,100)
        fills.observe(bar,100); self.assertEqual(fills.fills,[])
        fills.observe(replace(bar,close_time=t+timedelta(minutes=1),volume=0),100)
        self.assertEqual(fills.fills,[])
        fills.observe(replace(bar,close_time=t+timedelta(minutes=2)),100)
        self.assertEqual(len(fills.fills),1)

    def test_sparse_late_option_cannot_fake_round_trip(self):
        r=replay_case(CASE,bars('US.SPY'),bars(CASE['contract'])[379:380],SESSION)
        self.assertFalse(r['one_round_trip'])
        summary=metrics([r]); self.assertEqual(summary['failed'],1); self.assertIsNone(summary['total_option_pnl'])

    def test_missing_exit_does_not_reuse_entry_price(self):
        r=replay_case(CASE,bars('US.SPY'),bars(CASE['contract'])[:61],SESSION)
        self.assertIsNotNone(r['entry']); self.assertIsNone(r['exit']); self.assertFalse(r['one_round_trip'])

    def test_early_close_remains_same_session(self):
        session=replace(SESSION,closes=OPEN.replace(hour=13,minute=0))
        r=replay_case(CASE,bars('US.SPY',count=210),bars(CASE['contract'],count=210),session)
        self.assertTrue(r['one_round_trip']); self.assertEqual(r['exit']['at'],OPEN.replace(hour=12,minute=46).isoformat())

    def test_fallback_crosses_soft_spread_filter(self):
        s,j=self.service(); t=OPEN.replace(hour=10,minute=30)
        st=s.on_frame(j['id'],self.frame(t),t,Quote(CASE['contract'],.5,1,t))
        self.assertEqual(st['state'],'ENTRY'); self.assertEqual(st['orders'][0]['reason'],'deadline_fallback')

    def test_wide_spread_cannot_block_scheduled_exit(self):
        s,j=self.service(); t=OPEN.replace(hour=10,minute=30)
        st=s.on_frame(j['id'],self.frame(t),t,Quote(CASE['contract'],1,1,t))
        f=NextBarFills(s,j['id']);f.queue(st)
        f.observe(Bar(CASE['contract'],t+timedelta(minutes=1),1,1,1,1,100),100)
        end=OPEN.replace(hour=15,minute=45)
        st=s.heartbeat(j['id'],end,Quote(CASE['contract'],.5,1,end))
        self.assertEqual(st['orders'][-1]['side'],'SELL_CLOSE')

    def test_winners_have_no_fixed_take_profit(self):
        s,j=self.service(); j.update(entry_at=OPEN.isoformat(),entry_underlying=100,entry_atr=.1,best=100,position_qty=1)
        for i in range(1,50):
            f=replace(self.frame(OPEN+timedelta(minutes=i)),close=100+i*.1)
            self.assertIsNone(baseline_exit(j,f))
        self.assertGreater(j['best']-100,4)

    def test_idempotency_and_separate_contracts(self):
        s,j=self.service(); payload={**j['request']}
        self.assertEqual(s.create_job(payload,OPEN)['id'],j['id'])
        second=dict(CASE,contract='US.SPY260911C101000',strike=101)
        s.contracts=CaseCatalog(second)
        k=s.create_job(dict(payload,contract=second['contract']),OPEN)
        self.assertNotEqual(j['id'],k['id'])
        restart=CustodyService(s.path,'unit',CaseCatalog(second),CaseCalendar(SESSION),mode='dryrun')
        self.assertEqual(restart.get_job(j['id'])['request']['contract'],CASE['contract'])

    def test_old_database_migration_preserves_order_foreign_keys(self):
        s,j=self.service(); t=OPEN.replace(hour=10,minute=30)
        s.on_frame(j['id'],self.frame(t),t,Quote(CASE['contract'],1,1,t))
        db=sqlite3.connect(s.path)
        db.execute('CREATE TABLE old_jobs (id TEXT PRIMARY KEY,account TEXT NOT NULL,symbol TEXT NOT NULL,day TEXT NOT NULL,fingerprint TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(account,symbol,day))')
        db.execute('INSERT INTO old_jobs SELECT id,account,symbol,day,fingerprint,body FROM jobs')
        db.execute('DROP TABLE jobs');db.execute('ALTER TABLE old_jobs RENAME TO jobs');db.commit();db.close()
        restarted=CustodyService(s.path,'unit',CaseCatalog(CASE),CaseCalendar(SESSION),mode='dryrun')
        self.assertEqual(restarted.get_job(j['id'])['orders'][0]['side'],'BUY_OPEN')
        with sqlite3.connect(s.path) as db:self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_no_broker_dispatch_and_non_dryrun_rejected(self):
        s,j=self.service()
        with self.assertRaisesRegex(ValueError,'never dispatches'):s.dispatch_next(object(),OPEN)
        other=CustodyService(Path(self.tmp.name)/'paper.db','unit',CaseCatalog(CASE),CaseCalendar(SESSION))
        with self.assertRaisesRegex(ValueError,'dryrun-only'):other.create_job(j['request'],OPEN)

    def test_same_day_history_never_requests_daily_or_prior(self):
        rows=bars('US.SPY',count=30)
        class Market:
            def current_bars(self,*a,**kw):return rows
            def history_bars(self,*a,**kw):raise AssertionError('history not needed')
        source=SameDayHistorySource(Market(),CaseCalendar(SESSION),'US.SPY')
        observed,daily,closes=source.collect(rows[-1].close_time)
        self.assertEqual(len(observed),30);self.assertEqual(daily,[])

    def test_freeze_rejects_missing_or_changed_source(self):
        with self.assertRaisesRegex(ValueError,'requires'):verify_freeze(None)
        path=Path(self.tmp.name)/'freeze.json';path.write_text(json.dumps({'source_sha256':{'custody/timing.py':'0'*64}}))
        with self.assertRaisesRegex(ValueError,'changed'):verify_freeze(path)

    def test_format_keyed_manifest_checksums(self):
        root=Path(self.tmp.name)/'slice';root.mkdir()
        series=[]
        for code,kind in [('US.SPY','underlying'),(CASE['contract'],'option')]:
            path=root/(kind+'.csv');path.write_text('mechanical checksum fixture\n')
            series.append({'code':code,'kind':kind,'csv':path.name,
                           'sha256':{'csv':hashlib.sha256(path.read_bytes()).hexdigest()}})
        (root/'manifest.json').write_text(json.dumps({'role':'train/custody','series':series}))
        (root/'cases.json').write_text(json.dumps({'cases':[CASE]}))
        self.assertEqual(verify_slice(root)[2],2)
        (root/'option.csv').write_text('changed')
        with self.assertRaisesRegex(ValueError,'checksum mismatch'):verify_slice(root)


if __name__ == '__main__': unittest.main()
