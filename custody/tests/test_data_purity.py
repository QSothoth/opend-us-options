"""Data contamination and live-source recovery tests; synthetic OHLCV only."""
import builtins
import json
import tempfile
import unittest
from dataclasses import replace, asdict
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from custody.adaptive import AdaptiveIndicators, CandidateRegistry, make_strategy
from custody.signals import SignalProvider
from custody.dryrun import SameDayHistorySource
from custody.baseline import CaseCalendar, replay_case
from custody.tests.test_adaptive import tape
from custody.tests.test_baseline import OPEN, SESSION, CASE, DAY
from research.custody_v2 import data_boundary as boundary

class PurityTests(unittest.TestCase):
    def test_wrong_release_rejected_before_bar_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'manifest.json').write_text(json.dumps({'dataset':'custody-train-dte4','role':'train/custody','case_count':124}))
            with patch('custody.baseline.verify_slice',side_effect=AssertionError('must not read bars')) as reader:
                with self.assertRaisesRegex(ValueError,'identity/role'):boundary.training_slice(tmp)
                reader.assert_not_called()

    def test_cache_content_metadata_role_and_source_are_bound(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);cases=[{'case':i} for i in range(124)]
            np.savez_compressed(out/'cache.npz',features=np.zeros((6,124,391,31)),prices=np.zeros((124,391)),next_bar=np.zeros((124,391)),dtes=np.zeros(124),blocks=np.zeros(124))
            (out/'cache_metadata.json').write_text(json.dumps({'source':'custody-train-dte4','cases':cases}))
            boundary.bind_cache(out)
            originals={p.name:p.read_bytes() for p in out.iterdir()}
            with patch.object(boundary,'training_slice',return_value=({},cases,1)):
                self.assertEqual(boundary.load_training_cache('unused',out)[0].shape,(6,124,391,31))
                for filename in ('cache.npz','cache_metadata.json','CACHE_BINDING.json'):
                    with self.subTest(filename=filename):
                        if filename=='CACHE_BINDING.json':
                            b=json.loads(originals[filename]);b['role']='eval/custody';(out/filename).write_text(json.dumps(b))
                        else:(out/filename).write_bytes(originals[filename]+b' ')
                        with self.assertRaisesRegex(ValueError,'cache'):boundary.load_training_cache('unused',out)
                        (out/filename).write_bytes(originals[filename])
                with patch.object(boundary,'feature_source_hash',return_value='changed-code'):
                    with self.assertRaisesRegex(ValueError,'cache'):boundary.load_training_cache('unused',out)

    def test_provider_rejects_daily_prior_future_wrong_symbol_and_interval(self):
        strategy=make_strategy();provider=SignalProvider(CandidateRegistry(strategy));rows=[b.to_record() for b in tape('US.SPY')[:40]]
        def evaluate(bars,daily=None):return provider.evaluate(strategy['strategy_id'],'SPY','LONG',OPEN+timedelta(minutes=40),bars,daily or [],{DAY:SESSION.closes.isoformat()})
        expected=evaluate(rows)
        bad=[([{**rows[0],'close_time':(OPEN-timedelta(days=1)).isoformat()}]+rows,[]),
             (rows+[tape('US.SPY')[40].to_record()],[]),
             ([{**rows[0],'code':'US.QQQ'}]+rows[1:],[]),
             ([{**rows[0],'interval':'5m'}]+rows[1:],[]),
             (rows,[{'date':'2020-01-01','close':999999}])]
        for bars,daily in bad:
            with self.subTest(daily=bool(daily),first=bars[0]):
                with self.assertRaises(ValueError):evaluate(bars,daily)
        self.assertEqual(asdict(expected),asdict(evaluate(rows)))

    def test_runtime_does_not_import_research_or_numpy(self):
        strategy=make_strategy();provider=SignalProvider(CandidateRegistry(strategy));original=builtins.__import__
        def guarded(name,*a,**kw):
            if name.startswith(('research','numpy','numba','pandas')):raise AssertionError('runtime research dependency: '+name)
            return original(name,*a,**kw)
        with patch('builtins.__import__',side_effect=guarded):
            f=provider.evaluate(strategy['strategy_id'],'SPY','LONG',OPEN+timedelta(minutes=40),[b.to_record() for b in tape('US.SPY')[:40]],[],{DAY:SESSION.closes.isoformat()})
        self.assertEqual(f.bar_close,OPEN+timedelta(minutes=40))

    def test_replay_rejects_previous_day_tape(self):
        ub=tape('US.SPY');ob=tape(CASE['contract'],True)
        with self.assertRaisesRegex(ValueError,'same-session'):
            replay_case(CASE,[replace(ub[0],close_time=ub[0].close_time-timedelta(days=1))]+ub,ob,SESSION,strategy_override=make_strategy())

    def test_stream_failure_falls_back_only_to_today_and_recovers(self):
        class Market:
            def current_bars(self,*a,**k):raise RuntimeError('no subscription')
            def history_bars(self,code,interval,start,end,**kw):
                self.call=(interval,start,end);return tape(code)[:90]
        m=Market();src=SameDayHistorySource(m,CaseCalendar(SESSION),'US.SPY')
        bars,daily,_=src.collect(OPEN+timedelta(minutes=90))
        self.assertEqual((len(bars),daily,m.call),(90,[],('K_1M',DAY,DAY)))

    def test_midday_restart_and_incremental_cache_match(self):
        class Market:
            calls=0
            def current_bars(self,code,*a,**kw):
                n=int((kw['boundary']-OPEN).total_seconds()/60);return tape(code)[:n][-5:]
            def history_bars(self,code,*a,**kw):
                self.calls+=1;n=int((kw['boundary']-OPEN).total_seconds()/60);return tape(code)[:n]
        m=Market();src=SameDayHistorySource(m,CaseCalendar(SESSION),'US.SPY')
        src.collect(OPEN+timedelta(minutes=90));inc=src.collect(OPEN+timedelta(minutes=91));self.assertEqual(m.calls,1)
        restarted=SameDayHistorySource(Market(),CaseCalendar(SESSION),'US.SPY').collect(OPEN+timedelta(minutes=91))
        self.assertEqual(inc,restarted)

    def test_missing_minute_never_filled_from_previous_day(self):
        class Market:
            def current_bars(self,code,*a,**kw):return []
            def history_bars(self,code,*a,**kw):
                rows=tape(code)[:90];return rows[:20]+rows[21:]+[replace(rows[20],close_time=rows[20].close_time-timedelta(days=1))]
        with self.assertRaisesRegex(ValueError,'1 missing 1m'):
            SameDayHistorySource(Market(),CaseCalendar(SESSION),'US.SPY').collect(OPEN+timedelta(minutes=90))

    def test_rollover_discards_yesterday_and_indicator_state(self):
        class Calendar:
            def session(self,day):
                if day==DAY:return SESSION
                return replace(SESSION,day=day,opens=SESSION.opens+timedelta(days=1),closes=SESSION.closes+timedelta(days=1))
        class Market:
            def current_bars(self,code,*a,**kw):return tape(code)[:40]
            def history_bars(self,*a,**kw):return []
        src=SameDayHistorySource(Market(),Calendar(),'US.SPY');src.collect(OPEN+timedelta(minutes=40))
        with self.assertRaisesRegex(ValueError,'40 missing'):
            src.collect(OPEN+timedelta(days=1,minutes=40))
        eng=AdaptiveIndicators(make_strategy(),'SPY','LONG',SESSION)
        eng.step(tape('US.SPY')[0])
        with self.assertRaises(ValueError):eng.step(replace(tape('US.SPY')[1],close_time=OPEN+timedelta(days=1,minutes=2)))

if __name__=='__main__':unittest.main()
