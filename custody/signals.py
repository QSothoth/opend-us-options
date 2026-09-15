"""Reuse research indicators in an isolated worker; never alter host sys.path or sockets."""
from pathlib import Path
import json,subprocess,sys
from .models import Frame,instant,symbol
from .registry import Registry


class SignalProvider:
    def __init__(self,registry=None):self.registry=registry or Registry()

    def evaluate(self,strategy_id,underlying,direction,as_of,bars,daily,session_closes):
        """bars: completed1m OHLCV with aware ISO close_time; daily: completed dates.

        session_closes is trusted calendar data: date -> aware ISO session close.
        History must be consistent and contain no future bars/current daily OHLC.
        Caller supplies identical warmup history in replay and deployment.
        """
        item=self.registry.get(strategy_id);as_of=instant(as_of);underlying=symbol(underlying)
        if direction not in ('LONG','SHORT'):raise ValueError('invalid direction')
        if item.get('timing_model') in ('intraday_v1','intraday_v2'):
            from datetime import datetime, time
            from .models import ET, Session
            from .marketdata import Bar
            from .timing import IntradayIndicators
            if item.get('timing_model') == 'intraday_v2' and daily:
                raise ValueError('intraday_v2 forbids daily/cross-session OHLCV inputs')
            day = as_of.astimezone(ET).date()
            session = Session(day.isoformat(), datetime.combine(day,time(9,30),tzinfo=ET),
                              instant(session_closes[day.isoformat()]))
            if item.get('timing_model') == 'intraday_v2':
                from .adaptive import AdaptiveIndicators
                engine = AdaptiveIndicators(item, underlying, direction, session)
            else:
                engine = IntradayIndicators(item, underlying, direction, session)
            result = None
            for row in bars:
                t = instant(row['close_time'])
                if t > as_of: raise ValueError('future bar')
                if t.astimezone(ET).date() != day:
                    raise ValueError('baseline only accepts current-session bars')
                if item.get('timing_model') == 'intraday_v2':
                    if t <= session.opens or row.get('interval', '1m') != '1m':
                        raise ValueError('intraday_v2 requires regular-session 1m bars')
                    if symbol(row.get('code', underlying)) != underlying:
                        raise ValueError('wrong underlying in intraday input')
                if t <= session.opens: continue
                bar = Bar('US.'+underlying,t,**{k:row[k] for k in ['open','high','low','close','volume']})
                result = engine.step(bar)
            if result is None or result.bar_close != as_of:
                raise ValueError('latest completed 1m bar missing')
            return result
        payload={'case':item['config']['case'],'symbol':underlying,'direction':direction,'as_of':as_of.isoformat(),'bars':bars,'daily':daily,'session_closes':session_closes}
        worker=Path(__file__).resolve().parents[1]/'research/aggressive_payoff/code/feature_worker.py'
        result=subprocess.run([sys.executable,str(worker)],input=json.dumps(payload,allow_nan=False),text=True,capture_output=True,timeout=60)
        if result.returncode:raise ValueError('indicator history rejected: '+result.stderr.strip().splitlines()[-1])
        result=json.loads(result.stdout)
        return Frame(underlying,item['sha256'],instant(result['bar_close']),item['signal_minutes'],result['close'],result['daily_atr'],result['entry_ready'])
