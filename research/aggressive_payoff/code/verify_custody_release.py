"""Real Release regression: registry replay and completed-prefix feature parity."""
import run as runtime
import argparse,json,tempfile,sys
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from custody.registry import Registry
from custody.signals import SignalProvider
from custody.replay import replay as registered_replay
from data import load_release,make_cube,write_json
from engine import aggregate_1m,daily_atr
from features import Features

p=argparse.ArgumentParser();p.add_argument('--zip',required=True);p.add_argument('--out',required=True);a=p.parse_args()
frames,cal,audit,_=load_release(a.zip);raw,day=frames['klines_1m'],frames['klines_day'];reg=Registry();provider=SignalProvider(reg);dates=sorted(raw.date.unique());et=ZoneInfo('America/New_York');prefixes=[];replays=[]
for sid,reference in [('orb_rvol_rsi_1m_v1',ROOT/'research/aggressive_payoff/results/trades/posthoc_peak.csv'),('retest_rvol_adx_5m_v1',ROOT/'research/aggressive_payoff/five_minute_followup/trades/no_late_tightening.csv')]:
 item=reg.get(sid);e=item['config']['case']['entry'];step=e['signal_minutes'];cube=make_cube(aggregate_1m(raw,step),cal,step);ad=daily_atr(cube,day,20);bank=Features(cube,ad,day);signal=bank.entry(e)
 checks=[(15,630,'LONG'),(40,645,'SHORT'),(65,720,'LONG'),(82,750,'SHORT')]
 evidence=pd.read_csv(reference)
 for dr in ['LONG','SHORT']:
  positive=evidence[(evidence.symbol=='US.SPY')&(evidence.direction==dr)&evidence.traded].iloc[0]
  checks.append((dates.index(positive.date),int(positive.entry_signal_close_minute),dr))
 for index,minute,direction in checks:
  date=dates[index];now=datetime.fromisoformat(date).replace(hour=minute//60,minute=minute%60,tzinfo=et);g=raw[(raw.symbol=='US.SPY')&((raw.date<date)|((raw.date==date)&(raw.minute_close<=minute)))];bars=[]
  for r in g.itertuples():bars.append({'close_time':datetime.fromisoformat(r.date).replace(hour=r.minute_close//60,minute=r.minute_close%60,tzinfo=et).isoformat(),**{k:getattr(r,k) for k in ['open','high','low','close','volume']}})
  d=day[(day.symbol=='US.SPY')&(day.date<date)][['date','open','high','low','close']].to_dict('records');sessions={s:datetime.fromisoformat(s).replace(hour=13 if s in cal['early_close_dates'] else 16,tzinfo=et).isoformat() for s in dates[:index+1]}
  f=provider.evaluate(sid,'SPY',direction,now,bars,d,sessions);i=cube['keys'].index[(cube['keys'].date==date)&(cube['keys'].symbol=='US.SPY')][0];j=(minute-570)//step-1;di=0 if direction=='LONG' else 1
  assert f.entry_ready==bool(signal[i,di,j]);assert f.close==cube['close'][i,j];assert abs(f.daily_atr-ad[i])<1e-12
  prefixes.append({'strategy_id':sid,'date':date,'minute':minute,'direction':direction,'entry_ready':f.entry_ready})
 assert any(row['entry_ready'] for row in prefixes if row['strategy_id']==sid)
 # Explicit invalid-history checks use the last prefix, not synthetic evaluation.
 for badbars,baddaily in [(bars+[dict(bars[-1],close_time=(now+timedelta(minutes=1)).isoformat())],d),(bars[:-2]+bars[-1:],d),(bars,d+[dict(d[-1],date=date)])]:
  try:provider.evaluate(sid,'SPY',direction,now,badbars,baddaily,sessions)
  except ValueError:pass
  else:raise AssertionError('invalid/future history accepted')
 with tempfile.TemporaryDirectory() as tmp:
  registered_replay(sid,'SPY','LONG',a.zip,tmp);actual=pd.read_csv(Path(tmp)/'job_trades.csv');expected=pd.read_csv(reference);expected=expected[(expected.symbol=='US.SPY')&(expected.direction=='LONG')].reset_index(drop=True);assert_frame_equal(actual,expected,check_exact=False,rtol=1e-12,atol=1e-12)
  replays.append({'strategy_id':sid,'case_id':item['case_id'],'sessions':len(actual),'trades':int(actual.traded.sum()),'all_rows_match':True})
result={'passed':True,'data_tag':audit['tag'],'sha256':audit['sha256'],'registry_replays':replays,'completed_prefix_parity':prefixes,'future_missing_minute_and_current_daily_rejected':True,'scope':'Real underlying Release only; execution lifecycle mocks are separate engineering tests, not option PnL evidence.'};write_json(a.out,result);print(json.dumps(result),flush=True)
