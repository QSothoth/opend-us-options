import io,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta
from concurrent.futures import ThreadPoolExecutor
from custody.models import Contract,Session,Quote,Frame,OrderUpdate,ET,JobRequest
from custody.registry import Registry
from custody.service import CustodyService
from custody.http import create_app
DAY='2026-09-11'
T=datetime(2026,9,11,10,0,tzinfo=ET)
SID='orb_rvol_rsi_1m_v1'
class Catalog:
    def resolve(self,code):return Contract(code,'SPY',DAY,'PUT' if code=='TEST.PUT' else 'CALL',600)
class Calendar:
    def session(self,day):
        if day!=DAY:raise ValueError('session not configured')
        return Session(day,T.replace(hour=9,minute=30),T.replace(hour=16))
class Adapter:
    account='test';mode='paper'
    def __init__(self,now=T):self.now=now;self.calls=[];self.cancels=[]
    def submit(self,o):
        self.calls.append(o);return OrderUpdate(o['client_order_id'],0,'OPEN',0,self.now)
    def cancel(self,target,key):self.cancels.append((target,key))
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'jobs.sqlite';self.service=CustodyService(self.path,'test',Catalog(),Calendar());self.request={'strategy_id':SID,'symbol':'SPY','direction':'LONG','contract':'TEST.CALL','max_qty':2,'trade_date':DAY}
    def tearDown(self):self.tmp.cleanup()
    def job(self):return self.service.create_job(self.request,T)
    def quote(self,t=T):return Quote('TEST.CALL',1,1.05,t)
    def frame(self,t=T,close=100,ready=True):return Frame('SPY',self.service.registry.get(SID)['sha256'],t,1,close,2,ready)
    def entry(self):
        j=self.job();self.service.on_frame(j['id'],self.frame(),T,self.quote());a=Adapter();self.service.dispatch_next(a,T);return j,a,a.calls[0]['client_order_id']
    def fill(self,key,qty=2,status='FILLED',seq=1,when=T):return self.service.apply_update(OrderUpdate(key,seq,status,qty,when,100,1.05,T),when)
    def test_registry_exact_versions_and_copy(self):
        r=Registry();self.assertEqual(len(r.list()),2);x=r.get(SID);self.assertEqual(x['case_id'],'536f0789fb0d');x['config']['case']['entry']['cap_daily_atr']=999;self.assertEqual(r.get(SID)['config']['case']['entry']['cap_daily_atr'],.1);self.assertEqual(r.get('retest_rvol_adx_5m_v1')['case_id'],'2c0ecc056bd3')
    def test_minimal_input_and_strict_types(self):
        p={k:v for k,v in self.request.items() if k not in ('max_qty','trade_date')};r=JobRequest.parse(p,T);self.assertEqual(r.max_qty,1);self.assertEqual(r.trade_date,DAY)
        for bad in [dict(p,dry_run=False),dict(p,max_qty=True),dict(p,max_qty=1.5),dict(p,direction='SELL')]:
            with self.assertRaises(ValueError):JobRequest.parse(bad,T)
    def test_contract_validation(self):
        with self.assertRaises(ValueError):self.service.create_job(dict(self.request,contract='TEST.PUT'),T)
        r=JobRequest.parse(self.request,T)
        with self.assertRaises(ValueError):Contract('TEST.CALL','QQQ',DAY,'CALL',600).validate(r)
        with self.assertRaises(ValueError):Contract('TEST.CALL','SPY','2026-09-18','CALL',600).validate(r)
    def test_idempotency_concurrency_and_cross_strategy_lock(self):
        with ThreadPoolExecutor(max_workers=4) as pool:ids=list(pool.map(lambda _:self.job()['id'],range(8)))
        self.assertEqual(len(set(ids)),1)
        for change in [{'strategy_id':'retest_rvol_adx_5m_v1'},{'direction':'SHORT','contract':'TEST.PUT'},{'max_qty':1}]:
            with self.assertRaises(ValueError):self.service.create_job(dict(self.request,**change),T)
    def test_mode_is_bound_to_database(self):
        with self.assertRaises(ValueError):CustodyService(self.path,'test',Catalog(),Calendar(),mode='live')
    def test_restart_keeps_job_and_outbox(self):
        j,a,key=self.entry();self.service=CustodyService(self.path,'test',Catalog(),Calendar());self.assertEqual(self.job()['id'],j['id']);self.assertIsNone(self.service.dispatch_next(a,T));self.assertEqual(len(a.calls),1)
    def test_quote_and_frame_checks(self):
        j=self.job();bad=Quote('TEST.CALL',1,1.05,T-timedelta(seconds=10));self.service.on_frame(j['id'],self.frame(),T,bad);self.assertEqual(self.service.get_job(j['id'])['orders'],[])
        with self.assertRaises(ValueError):self.service.on_frame(j['id'],self.frame(T+timedelta(minutes=1)),T,self.quote())
        with self.assertRaises(ValueError):self.service.on_frame(j['id'],self.frame(T-timedelta(minutes=1)),T,self.quote())
    def test_unknown_submission_is_not_retried(self):
        j=self.job();self.service.on_frame(j['id'],self.frame(),T,self.quote())
        class Timeout(Adapter):
            def submit(self,o):self.calls.append(o);raise TimeoutError()
        a=Timeout();r=self.service.dispatch_next(a,T);self.assertIn('unknown',r);self.assertIsNone(self.service.dispatch_next(a,T));self.assertEqual(len(a.calls),1);self.assertEqual(self.service.get_job(j['id'])['orders'][0]['status'],'UNKNOWN')
    def test_partial_entry_cancel_ack_is_not_terminal(self):
        j,a,key=self.entry();self.fill(key,1,'PARTIAL');later=T+timedelta(minutes=6);self.service.on_frame(j['id'],self.frame(later,99.9),later,self.quote(later));self.service.dispatch_next(a,later)
        state=self.service.get_job(j['id']);self.assertEqual(state['position_qty'],1);self.assertEqual(state['state'],'EXIT');self.assertFalse(any(o['side']=='SELL_CLOSE' for o in state['orders']))
        self.fill(key,1,'CANCELED',2,later);self.service.heartbeat(j['id'],later,self.quote(later));sell=[o for o in self.service.get_job(j['id'])['orders'] if o['side']=='SELL_CLOSE'][0];self.assertEqual(sell['quantity'],1)
    def test_partial_exit_only_sells_remaining(self):
        j,a,key=self.entry();self.fill(key);later=T+timedelta(minutes=6);self.service.on_frame(j['id'],self.frame(later,99.9),later,self.quote(later));sell=self.service.get_job(j['id'])['orders'][-1]
        self.service.apply_update(OrderUpdate(sell['client_order_id'],0,'CANCELED',1,later,None,1),later);self.service.heartbeat(j['id'],later,self.quote(later));last=self.service.get_job(j['id'])['orders'][-1];self.assertEqual(last['quantity'],1);self.assertTrue(last['reduce_only'])
        self.service.apply_update(OrderUpdate(last['client_order_id'],0,'FILLED',1,later,None,1),later);self.assertEqual(self.service.get_job(j['id'])['state'],'DONE')
        with self.assertRaises(ValueError):self.service.create_job(dict(self.request,max_qty=1),later)
    def test_duplicate_and_inconsistent_fill(self):
        j,a,key=self.entry();self.fill(key);self.fill(key);self.assertEqual(self.service.get_job(j['id'])['position_qty'],2)
        with self.assertRaises(ValueError):self.fill(key,1,'PARTIAL')
        with self.assertRaises(ValueError):self.fill(key,3,'FILLED',2)
    def test_flatten_without_new_bars_and_no_quote(self):
        j,a,key=self.entry();self.fill(key);end=T.replace(hour=15,minute=45);self.service.heartbeat(j['id'],end);s=self.service.get_job(j['id']);self.assertEqual(s['state'],'EXIT');self.assertEqual(s['attention'],'EXIT_WAITING_VALID_QUOTE')
        self.service.heartbeat(j['id'],end,self.quote(end));self.assertEqual(self.service.get_job(j['id'])['orders'][-1]['side'],'SELL_CLOSE')
    def test_unsent_entry_is_canceled_locally_at_flatten(self):
        j=self.job();self.service.on_frame(j['id'],self.frame(),T,self.quote());end=T.replace(hour=15,minute=45);s=self.service.heartbeat(j['id'],end,self.quote(end));self.assertEqual(s['state'],'DONE');self.assertEqual(len(s['orders']),1);self.assertEqual(s['orders'][0]['status'],'CANCELED')
    def test_authenticated_minimal_http(self):
        token='test-only-bearer-token-123';app=create_app(self.service,token,lambda:T);status=[]
        def call(payload,auth):
            body=json.dumps(payload).encode();return app({'REQUEST_METHOD':'POST','PATH_INFO':'/v1/jobs','CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(body)),'wsgi.input':io.BytesIO(body),'HTTP_AUTHORIZATION':auth},lambda s,h:status.append(s))
        call(self.request,'');self.assertEqual(status[-1],'401 Unauthorized');response=call(self.request,'Bearer '+token);self.assertEqual(status[-1],'200 OK');self.assertEqual(json.loads(response[0])['state'],'IDLE')
    def test_controller_paper_round_trip(self):
        from custody.demo import run_demo
        result=run_demo(Path(self.tmp.name)/'demo.sqlite');self.assertTrue(result['one_round_trip']);self.assertEqual([o['side'] for o in result['orders']],['BUY_OPEN','SELL_CLOSE'])
    def test_stop_watch_does_not_create_an_order(self):
        j=self.job();stopped=self.service.stop_job(j['id'],T);self.assertEqual(stopped['state'],'DONE');self.assertEqual(stopped['orders'],[])
    def test_short_direction_put_and_five_minute_boundary(self):
        r=dict(self.request,strategy_id='retest_rvol_adx_5m_v1',direction='SHORT',contract='TEST.PUT');j=self.service.create_job(r,T);h=j['strategy']['sha256']
        with self.assertRaises(ValueError):self.service.on_frame(j['id'],Frame('SPY',h,T+timedelta(minutes=1),5,100,2,True),T+timedelta(minutes=1),Quote('TEST.PUT',1,1.05,T+timedelta(minutes=1)))
        self.service.on_frame(j['id'],Frame('SPY',h,T,5,100,2,True),T,Quote('TEST.PUT',1,1.05,T));self.assertEqual(self.service.get_job(j['id'])['orders'][0]['side'],'BUY_OPEN')
    def test_previous_unresolved_session_blocks_new_day(self):
        self.job()
        with self.assertRaisesRegex(ValueError,'previous session'):
            self.service.create_job(dict(self.request,trade_date='2026-09-14'),T.replace(day=14))
    def test_broker_lookup_failure_does_not_skip_flatten_cancel(self):
        from custody.controller import Controller
        j,a,key=self.entry()
        def broken(key):raise TimeoutError()
        a.lookup=broken;end=T.replace(hour=15,minute=45)
        state=Controller(self.service,a).step(j['id'],end,self.quote(end));self.assertEqual(state['state'],'EXIT');self.assertEqual(len(a.cancels),1);self.assertEqual(state['attention'],'RECONCILE_ORDER_STATUS')
if __name__=='__main__':unittest.main()
