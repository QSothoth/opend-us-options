"""Completed-history indicator worker; no order execution, no future padding prices."""
import os,socket
os.environ['OPENBLAS_NUM_THREADS']='1'
def blocked(*a,**k):raise RuntimeError('offline indicator worker')
socket.create_connection=blocked;socket.getaddrinfo=blocked
_original=socket.socket.connect
def connect(sock,address):
    if sock.family in (socket.AF_INET,socket.AF_INET6):raise RuntimeError('offline indicator worker')
    return _original(sock,address)
socket.socket.connect=connect
import json,sys,math
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from data import make_cube
from engine import aggregate_1m,daily_atr
from features import Features
ET=ZoneInfo('America/New_York')

def stamp(s):
    d=datetime.fromisoformat(s.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('timezone-aware close timestamp required')
    return d.astimezone(ET)

def evaluate(p):
    e=p['case']['entry'];now=stamp(p['as_of']);minute=now.hour*60+now.minute;step=e['signal_minutes'];sym='US.'+p['symbol']
    if now.second or now.microsecond or not 570<minute<=960 or (minute-570)%step:raise ValueError('as_of must be a completed native bar boundary')
    rows=[]
    for r in p['bars']:
        t=stamp(r['close_time'])
        if t>now or t.second or t.microsecond:raise ValueError('future or malformed minute bar')
        if any(type(r[k]) not in (int,float) or not math.isfinite(r[k]) for k in ['open','high','low','close','volume']):raise ValueError('nonfinite bar')
        if min(r[k] for k in ['open','high','low','close'])<=0 or r['volume']<0 or r['low']>min(r['open'],r['close']) or r['high']<max(r['open'],r['close']):raise ValueError('invalid OHLCV')
        rows.append({**{k:r[k] for k in ['open','high','low','close','volume']},'symbol':sym,'date':t.date().isoformat(),'minute_close':t.hour*60+t.minute,'time':int(pd.Timestamp(t.replace(tzinfo=None)).value//10**9)})
    f=pd.DataFrame(rows)
    if f.empty or f.duplicated(['date','minute_close']).any():raise ValueError('empty or duplicate bars')
    f=f.sort_values(['date','minute_close']);early=[]
    expected_days=sorted(day for day in p['session_closes'] if f.iloc[0]['date']<=day<=now.date().isoformat())
    if sorted(f.date.unique())!=expected_days:raise ValueError('missing complete trading session')
    for day,g in f.groupby('date'):
        if day not in p['session_closes']:raise ValueError('missing trusted session calendar')
        end=stamp(p['session_closes'][day])
        if end.date().isoformat()!=day or end.second or end.microsecond:raise ValueError('invalid session close')
        close=end.hour*60+end.minute
        if close not in (780,960):raise ValueError('unsupported session hours')
        if close==780:early.append(day)
        until=min(close,minute) if day==now.date().isoformat() else close
        if g.minute_close.tolist()!=list(range(571,until+1)):raise ValueError('missing/out-of-session minute bars')
    if f.iloc[-1]['date']!=now.date().isoformat() or f.iloc[-1].minute_close!=minute:raise ValueError('latest closed bar missing')
    d=pd.DataFrame(p['daily'])
    if d.empty or d.duplicated('date').any() or any(day>=now.date().isoformat() for day in d.date):raise ValueError('daily input must contain prior completed dates only')
    for r in d.to_dict('records'):
        if any(type(r[k]) not in (int,float) or not math.isfinite(r[k]) or r[k]<=0 for k in ['open','high','low','close']):raise ValueError('invalid daily OHLC')
        if r['low']>min(r['open'],r['close']) or r['high']<max(r['open'],r['close']):raise ValueError('invalid daily range')
    # Structural current-date row only: all prices missing, then shifted by the
    # unchanged dailyATR function. It supplies a join key, never a synthetic price.
    d=pd.concat([d,pd.DataFrame([{'date':now.date().isoformat(),'open':np.nan,'high':np.nan,'low':np.nan,'close':np.nan}])],ignore_index=True)
    d['symbol']=sym;d['time']=pd.to_datetime(d.date).astype('int64')//10**9;d=d.sort_values('time')
    cube=make_cube(aggregate_1m(f,step),{'early_close_dates':early},step);ad=daily_atr(cube,d,e['daily_atr_days']);bank=Features(cube,ad,d)
    # The research Bank initializes paths for its fixed ten-symbol universe.
    # A live Job supplies one underlying: build only its observed paths, avoiding
    # empty ADX streams and supporting a resolver-validated additional symbol.
    bank.bank.paths=[]
    for observed in cube['keys'].symbol.unique():
        indices=np.flatnonzero(cube['keys'].symbol.to_numpy()==observed);rr,cc=np.where(np.isfinite(cube['close'][indices]));bank.bank.paths.append((indices[rr],cc))
    if 'rs' in e['gates']:raise ValueError('relative-strength strategy requires a multi-symbol provider')
    i=len(cube['keys'])-1;j=(minute-570)//step-1;direction=0 if p['direction']=='LONG' else 1
    return {'bar_close':now.isoformat(),'close':float(cube['close'][i,j]),'daily_atr':float(ad[i]),'entry_ready':bool(bank.entry(e)[i,direction,j])}

if __name__=='__main__':print(json.dumps(evaluate(json.load(sys.stdin)),allow_nan=False))
