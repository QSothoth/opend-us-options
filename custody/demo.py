"""Deterministic paper lifecycle demo; fabricated quotes are engineering fixtures only."""
from datetime import datetime,timedelta
from pathlib import Path
import json,tempfile
from .models import Contract,Session,Frame,Quote,OrderUpdate,ET
from .service import CustodyService
from .controller import Controller


def run_demo(db):
    now=datetime(2026,9,11,10,0,tzinfo=ET)
    class Catalog:
        def resolve(self,code):return Contract(code,'SPY','2026-09-11','CALL',600)
    class Calendar:
        def session(self,day):return Session(day,now.replace(hour=9,minute=30),now.replace(hour=16))
    class Paper:
        account='demo';mode='paper'
        def __init__(self):self.events={};self.now=now;self.mark=100
        def submit(self,intent):
            event=OrderUpdate(intent['client_order_id'],0,'FILLED',intent['quantity'],self.now,self.mark,intent['limit_price'],self.now if intent['side']=='BUY_OPEN' else None);self.events[intent['client_order_id']]=event;return event
        def lookup(self,key):return self.events.get(key)
        def cancel(self,target,key):raise AssertionError('fully filled demo orders cannot require cancel')
    service=CustodyService(db,'demo',Catalog(),Calendar());request={'strategy_id':'orb_rvol_rsi_1m_v1','symbol':'SPY','direction':'LONG','contract':'DEMO_OPTION'};j=service.create_job(request,now);paper=Paper();controller=Controller(service,paper);h=j['strategy']['sha256'];states=[j['state']]
    j=controller.step(j['id'],now,Quote('DEMO_OPTION',1,1.05,now),Frame('SPY',h,now,1,100,2,True));states.append(j['state'])
    later=now+timedelta(minutes=1);paper.now=later;paper.mark=99.9
    j=controller.step(j['id'],later,Quote('DEMO_OPTION',.9,.95,later),Frame('SPY',h,later,1,99.9,2,False));states.append(j['state'])
    assert j['state']=='DONE' and j['position_qty']==0
    return {'engineering_demo_only':True,'real_quotes':False,'orders':[{'side':o['side'],'quantity':o['quantity'],'status':o['status']} for o in j['orders']],'states_observed':states,'position_qty':j['position_qty'],'one_round_trip':True}

if __name__=='__main__':
    with tempfile.TemporaryDirectory() as tmp:print(json.dumps(run_demo(Path(tmp)/'paper.sqlite'),indent=2))
