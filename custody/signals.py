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
        payload={'case':item['config']['case'],'symbol':underlying,'direction':direction,'as_of':as_of.isoformat(),'bars':bars,'daily':daily,'session_closes':session_closes}
        worker=Path(__file__).resolve().parents[1]/'research/aggressive_payoff/code/feature_worker.py'
        result=subprocess.run([sys.executable,str(worker)],input=json.dumps(payload,allow_nan=False),text=True,capture_output=True,timeout=60)
        if result.returncode:raise ValueError('indicator history rejected: '+result.stderr.strip().splitlines()[-1])
        result=json.loads(result.stdout)
        return Frame(underlying,item['sha256'],instant(result['bar_close']),item['signal_minutes'],result['close'],result['daily_atr'],result['entry_ready'])
