"""Same-session, causal 1m indicators for the must-trade custody baseline.

No option prices, previous sessions, scenario labels, network or daily bars enter
this module. A fresh instance is required for each underlying/session/direction.
"""
from collections import deque
from datetime import timedelta
import math

from .models import ET, Frame, instant, positive, symbol


class IntradayIndicators:
    def __init__(self, strategy, underlying, direction, session):
        if direction not in ('LONG', 'SHORT'):
            raise ValueError('invalid direction')
        self.strategy, self.symbol = strategy, symbol(underlying)
        self.sign = 1 if direction == 'LONG' else -1
        self.session = session
        self.cfg = strategy['config']['case']['entry']
        self.rows = deque(maxlen=60)
        self.trs = deque(maxlen=self.cfg['atr_period'])
        self.gains = deque(maxlen=self.cfg['rsi_period'])
        self.losses = deque(maxlen=self.cfg['rsi_period'])
        self.plus = deque(maxlen=self.cfg['adx_period'])
        self.minus = deque(maxlen=self.cfg['adx_period'])
        self.adx_tr = deque(maxlen=self.cfg['adx_period'])
        self.dx = deque(maxlen=self.cfg['adx_period'])
        self.fast = self.slow = None
        self.volume = self.pv = 0.0
        self.or_high, self.or_low = -math.inf, math.inf
        self.last = None
        self.count = 0

    def step(self, bar):
        t = instant(bar.close_time)
        opened, closed = self.session.opens, self.session.closes
        if symbol(bar.code) != self.symbol or bar.interval != '1m':
            raise ValueError('wrong underlying or interval')
        elapsed = (t - opened).total_seconds()
        if not opened < t <= closed or elapsed % 60:
            raise ValueError('bar outside native session boundary')
        if self.last and t <= self.last:
            raise ValueError('duplicate or out-of-order bar')
        for k in ('open', 'high', 'low', 'close'):
            positive(getattr(bar, k), k)
        if self.last and t != self.last + timedelta(minutes=1):
            # Missing bars are never synthesized. Decisions resume on observations.
            gap = True
        else:
            gap = False
        prev = self.rows[-1] if self.rows else None
        change = bar.close - prev.close if prev else 0.0
        tr = max(bar.high - bar.low, abs(bar.high - prev.close),
                 abs(bar.low - prev.close)) if prev else bar.high - bar.low
        self.trs.append(tr)
        self.gains.append(max(change, 0)); self.losses.append(max(-change, 0))
        up = bar.high - prev.high if prev else 0.0
        down = prev.low - bar.low if prev else 0.0
        self.plus.append(up if up > down and up > 0 else 0.0)
        self.minus.append(down if down > up and down > 0 else 0.0)
        self.adx_tr.append(tr)
        denom = sum(self.plus) + sum(self.minus)
        self.dx.append(100 * abs(sum(self.plus) - sum(self.minus)) / denom if denom else 0.0)
        # Explicit rolling-window ADX approximation, not Wilder's cross-day ADX.
        adx = sum(self.dx) / len(self.dx)
        gains, losses = sum(self.gains), sum(self.losses)
        rsi = 100 * gains / (gains + losses) if gains + losses else 50.0
        old_fast = self.fast
        self.fast = bar.close if self.fast is None else self.fast + 2 / (self.cfg['ema_fast'] + 1) * (bar.close - self.fast)
        self.slow = bar.close if self.slow is None else self.slow + 2 / (self.cfg['ema_slow'] + 1) * (bar.close - self.slow)
        typical = (bar.high + bar.low + bar.close) / 3
        self.pv += typical * bar.volume; self.volume += bar.volume
        vwap = self.pv / self.volume if self.volume else bar.close
        volumes = [r.volume for r in list(self.rows)[-self.cfg['volume_window']:]]
        vol_base = sum(volumes) / len(volumes) if volumes else 0
        volume_ratio = bar.volume / vol_base if vol_base > 0 else 0.0
        if elapsed <= 60 * self.cfg['opening_minutes']:
            self.or_high = max(self.or_high, bar.high); self.or_low = min(self.or_low, bar.low)
        level = self.or_high if self.sign == 1 else self.or_low
        breakout = math.isfinite(level) and self.sign * (bar.close - level) > 0
        reclaim = prev is not None and self.sign * (prev.close - vwap) <= 0 < self.sign * (bar.close - vwap)
        atr = max(sum(self.trs) / len(self.trs), bar.close * 0.00001)
        votes = {
            'opening_breakout_or_vwap_reclaim': bool(breakout or reclaim),
            'vwap_side': self.sign * (bar.close - vwap) > 0,
            'ema_trend': self.sign * (self.fast - self.slow) > 0 and
                         (old_fast is None or self.sign * (self.fast - old_fast) > 0),
            'rsi': self.sign * (rsi - 50) >= self.cfg['rsi_distance'],
            'adx_direction': adx >= self.cfg['adx_min'] and self.sign * (sum(self.plus) - sum(self.minus)) > 0,
            'intraday_volume_acceleration': volume_ratio >= self.cfg['volume_ratio_min'],
        }
        score = sum(votes.values())
        minute = t.astimezone(ET).hour * 60 + t.astimezone(ET).minute
        threshold = self.cfg['score_early'] if minute < self.cfg['relax_at'] else self.cfg['score_late']
        warmed = elapsed >= 60 * self.cfg['warmup_minutes'] and self.count + 1 >= self.cfg['warmup_minutes']
        ready = bool(warmed and score >= threshold)
        trend_against = self.sign * (bar.close - vwap) < 0 and self.sign * (self.fast - self.slow) < 0
        details = {'score': score, 'votes': votes, 'vwap': vwap, 'ema_fast': self.fast,
                   'ema_slow': self.slow, 'rsi': rsi, 'rolling_adx': adx,
                   'intraday_volume_ratio': volume_ratio, 'atr_1m': atr,
                   'gap_before_bar': gap, 'observed_bars': self.count + 1}
        self.rows.append(bar); self.last = t; self.count += 1
        return Frame(self.symbol, self.strategy['sha256'], t, 1, bar.close, atr,
                     ready, trend_against=bool(trend_against),
                     entry_reason='indicator_score', diagnostics=details)


def baseline_exit(job, frame):
    """Update a held position using underlying closed bars only; no fixed TP."""
    x = job['strategy']['config']['case']['exit']
    sign = 1 if job['request']['direction'] == 'LONG' else -1
    job['best'] = max(job['best'], sign * frame.close)
    profit = sign * (frame.close - job['entry_underlying'])
    gain = job['best'] - sign * job['entry_underlying']
    atr = job['entry_atr']
    held = (instant(frame.bar_close) - instant(job['entry_at'])).total_seconds() / 60
    job['against_count'] = job.get('against_count', 0) + 1 if frame.trend_against else 0
    if profit <= -x['hard_stop_atr'] * atr:
        return 'underlying_safety'
    if (x['failure_minutes'] and held >= x['failure_minutes'] and profit <= 0
            and job['against_count'] >= x['failure_confirmations']):
        return 'failed_followthrough'
    if (x['trail_atr'] and gain >= x['trail_activation_atr'] * atr
            and job['best'] - sign * frame.close >= x['trail_atr'] * atr):
        return 'trailing'
    return None
