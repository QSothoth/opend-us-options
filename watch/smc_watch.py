#!/usr/bin/env python3
"""Intraday tape watcher: VWAP + EMA9/21 + RSI14 bias, SMC structure triggers.

Bias (unchanged from the original): price vs session VWAP, EMA9/21, RSI14.
Triggers:
    BOS    break of structure through a swing confirmed on BOTH sides
    FVG    3-bar fair value gap wider than FVG_ATR x ATR14
    SWEEP  wick takes out a confirmed swing then closes back inside (stop hunt)

Every trigger that passes the bias is marked. Volume expansion and the
premium/discount half of the day's range do NOT decide whether a mark fires --
they only split `plain` marks from `vol` ones, and measurement says `vol` marks
are rarer, not better (see README).

Read-only OpenD quotes, any number of codes. Every poll re-reads the day's
closed 1m bars and derives the whole mark list from them, so nothing accumulates
in memory and a mid-session restart reproduces the same state.

    ./smc_watch.py                      # default code
    ./smc_watch.py HK.02513 HK.09988
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
import unicodedata
from pathlib import Path

logging.getLogger('futu').setLevel(logging.ERROR)
logging.getLogger().setLevel(logging.ERROR)

from futu import AuType, KLType, OpenQuoteContext, RET_OK, SubType  # noqa: E402

DEFAULT_CODES = ['HK.02513']
HOST = '127.0.0.1'
PORT = 11111
POLL_SEC = 30
KLINE_BACK = 400        # > one HK session of 1m bars
SWING_LEFT = 3          # bars confirmed on BOTH sides of a swing
VOL_MULT = 1.5          # trigger bar volume vs the trailing 20-bar mean
VOL_LOOKBACK = 20
FVG_ATR = 0.25          # gap must be this fraction of ATR14 to count
ATR_LEN = 14
PREMIUM = 0.5           # longs below this much of the day's range, shorts above
WARMUP_BARS = 25
DEDUP_BARS = 15         # per direction
STALE_POLLS = 4
LAST_BAR = ' 16:00'     # the 16:00 bar is the closing auction, not continuous trade
RECENT_MARKS = 8
HKT = timezone(timedelta(hours=8))
LOG_DIR = Path(__file__).resolve().parent / 'logs'

DIM, GREEN, RED, BOLD, OFF = '\033[2m', '\033[32m', '\033[31m', '\033[1m', '\033[0m'
BLOCKS = '▁▂▃▄▅▆▇█'


def now_hkt():
    return datetime.now(HKT)


def close_hkt():
    return now_hkt().replace(hour=16, minute=10, second=0, microsecond=0)


def log_path():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / ('%s.log' % now_hkt().strftime('%Y-%m-%d'))


def emit(kind, payload):
    line = json.dumps({'kind': kind, 'payload': payload}, ensure_ascii=True, default=str)
    with open(log_path(), 'a', encoding='utf-8') as fh:
        fh.write(line + '\n')
    if not sys.stdout.isatty():
        print(line, flush=True)


def frame_name(frame):
    """OpenD ships the display name on every kline row; use it, do not re-query."""
    if frame is None or len(frame) == 0 or 'name' not in frame.columns:
        return ''
    return str(frame['name'][0])


def frame_bars(frame):
    if frame is None or len(frame) == 0:
        return []
    times = frame['time_key'] if 'time_key' in frame.columns else frame['time']
    out = []
    for t, o, h, l, c, v in zip(
        times, frame['open'], frame['high'], frame['low'], frame['close'], frame['volume'],
    ):
        row = (str(t), float(o), float(h), float(l), float(c), float(v or 0))
        if all(x == x for x in row[1:]):  # NaN poisons VWAP into silence
            out.append(row)
    return out


def session_bars(ctx, code):
    """(today's CLOSED 1m bars, display name), or (None, '') on failure.

    The minute still being traded is dropped: OpenD returns it half-built, and a
    mark fired on it would be emitted and then quietly stop being true once the
    bar completes.
    """
    ret, frame = ctx.get_cur_kline(code, KLINE_BACK, KLType.K_1M, AuType.NONE)
    if ret != RET_OK:
        return None, ''
    now = now_hkt()
    day = now.strftime('%Y-%m-%d')
    bars = [b for b in frame_bars(frame) if day <= b[0] < day + LAST_BAR]
    if bars and bars[-1][0].startswith(now.strftime('%Y-%m-%d %H:%M')):
        bars.pop()
    return bars, frame_name(frame)


def ema_last(closes, period):
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    prev = sum(closes[:period]) / period
    for v in closes[period:]:
        prev = v * k + prev * (1.0 - k)
    return prev


def rsi_last(closes, period=14):
    if len(closes) <= period:
        return None
    avg_g = avg_l = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        avg_g += max(d, 0.0)
        avg_l += max(-d, 0.0)
    avg_g /= period
    avg_l /= period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_g / avg_l)


def vwap_last(bars):
    pv = vol = 0.0
    for _t, _o, h, l, c, v in bars:
        pv += ((h + l + c) / 3.0) * v
        vol += v
    return pv / vol if vol > 0 else None


def last_swing(highs, lows, left=SWING_LEFT):
    """Last swing with `left` bars confirmed on BOTH sides.

    An unconfirmed swing sits at or above the latest high, which makes
    `close > swing_high` unreachable -- that bug kept BOS silent all day.
    """
    sh = sl = None
    for j in range(left, len(highs) - left):
        if highs[j] == max(highs[j - left:j + left + 1]):
            sh = highs[j]
        if lows[j] == min(lows[j - left:j + left + 1]):
            sl = lows[j]
    return sh, sl


def read(bars):
    """Indicators plus the volume / structure context for the latest bar."""
    closes = [b[4] for b in bars]
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]
    d = {'t': bars[-1][0], 'close': closes[-1], 'n': len(bars),
         'vwap': vwap_last(bars), 'ema9': ema_last(closes, 9),
         'ema21': ema_last(closes, 21), 'rsi': rsi_last(closes)}
    win = bars[-VOL_LOOKBACK:]
    avg_v = sum(b[5] for b in win) / len(win)
    d['vol_ratio'] = bars[-1][5] / avg_v if avg_v > 0 else 0.0
    atr_win = bars[-ATR_LEN:]
    d['atr'] = sum(b[2] - b[3] for b in atr_win) / len(atr_win)
    hi, lo = max(highs), min(lows)
    d['day_hi'], d['day_lo'] = hi, lo
    d['pos'] = (closes[-1] - lo) / (hi - lo) if hi > lo else 0.5
    d['swing_high'], d['swing_low'] = last_swing(highs[-24:], lows[-24:])
    return d


def structure(bars, d):
    """(bull triggers, bear triggers) on the latest bar. Volume is NOT judged
    here; `marks` uses it only to split `plain` marks from `vol` ones."""
    if d['atr'] <= 0:
        return [], []
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]
    close = d['close']
    bull, bear = [], []
    sh, sl = d['swing_high'], d['swing_low']
    if sh is not None and close > sh:
        bull.append('BOS')
    if sl is not None and close < sl:
        bear.append('BOS')
    if len(bars) >= 3:
        gap_up = lows[-1] - highs[-3]
        gap_dn = lows[-3] - highs[-1]
        if gap_up > FVG_ATR * d['atr']:
            bull.append('FVG')
        elif gap_dn > FVG_ATR * d['atr']:
            bear.append('FVG')
    # liquidity sweep: wick through a confirmed swing, close back inside
    if sl is not None and lows[-1] < sl <= close:
        bull.append('SWEEP')
    if sh is not None and highs[-1] > sh >= close:
        bear.append('SWEEP')
    return bull, bear


def marks(bars):
    """Two tiers of marks, derived purely from `bars`.

    plain  bias + structure trigger. ~14/day per code. Noisy on purpose: plenty
           of false positives, but it is what shows the tape is doing something.
    vol    the same trigger with volume expansion AND in the right half of the
           day's range. ~1.2/day.

    Measured over 4 codes x 40 days, `vol` marks are NOT more accurate than
    `plain` ones -- only rarer. The tier says what fired, not how reliable.
    """
    out, last, d = [], {}, None
    for end in range(1, len(bars) + 1):
        i = end - 1
        d = read(bars[:end])
        if None in (d['vwap'], d['ema9'], d['ema21'], d['rsi']) or i < WARMUP_BARS:
            continue
        bull, bear = structure(bars[:end], d)
        long_bias = d['close'] > d['vwap'] and d['ema9'] > d['ema21'] and d['rsi'] >= 50
        short_bias = d['close'] < d['vwap'] and d['ema9'] < d['ema21'] and d['rsi'] <= 50
        side = trig = None
        if long_bias and bull:
            side, trig, ok_zone = 'BUY', '+'.join(bull), d['pos'] <= PREMIUM
        elif short_bias and bear:
            side, trig, ok_zone = 'SELL', '+'.join(bear), d['pos'] >= 1 - PREMIUM
        if side is None:
            continue
        tier = 'vol' if (d['vol_ratio'] >= VOL_MULT and ok_zone) else 'plain'
        key = (side, tier)
        if i - last.get(key, -10 ** 9) < DEDUP_BARS:
            continue
        last[key] = i
        out.append({'i': i, 't': bars[i][0][11:16], 'side': side, 'tier': tier,
                    'trigger': trig, 'close': d['close'], 'rsi': round(d['rsi'], 1),
                    'vwap': round(d['vwap'], 3), 'vol_ratio': round(d['vol_ratio'], 2),
                    'pos': round(d['pos'], 2),
                    'stop': d['swing_low'] if side == 'BUY' else d['swing_high']})
    return out, d


def spark(bars, width):
    """Session close path as blocks, scaled to the day's own range."""
    lo = min(b[3] for b in bars)
    span = (max(b[2] for b in bars) - lo) or 1.0
    return ''.join(
        BLOCKS[min(7, int((bars[max(x * len(bars) // width + 1,
                                    (x + 1) * len(bars) // width) - 1][4] - lo) / span * 8))]
        for x in range(width))


def vol_row(bars, width):
    """Volume per time slot, same scale as the curve above it."""
    slots = []
    for x in range(width):
        a = x * len(bars) // width
        b = max(a + 1, (x + 1) * len(bars) // width)
        slots.append(sum(k[5] for k in bars[a:b]) / (b - a))
    top = max(slots) or 1.0
    return ''.join(BLOCKS[min(7, int(s / top * 8))] for s in slots)


def mark_row(bars, ms, width):
    """All marks on one row, aligned to the curve above it.

    One slot is about six minutes. When several marks land in a slot the
    EARLIEST one wins -- where a move started matters more than that it kept
    going -- and a volume-confirmed mark upgrades the slot to upper case.
    Every mark, collided or not, is still listed in full underneath.
    """
    slot = {}
    for m in ms:
        x = m['i'] * width // len(bars)
        if x in slot:
            if m['tier'] == 'vol':
                slot[x] = (slot[x][0], True)   # keep the earlier side, raise the case
            continue
        slot[x] = ('B' if m['side'] == 'BUY' else 'S', m['tier'] == 'vol')
    if not slot:
        return ''
    out = []
    for x in range(width):
        if x not in slot:
            out.append(DIM + '·' + OFF)
            continue
        ch, strong = slot[x]
        col = GREEN if ch == 'B' else RED
        out.append(col + (BOLD + ch if strong else DIM + ch.lower()) + OFF)
    return ''.join(out)


def disp_width(text):
    """Terminal columns a string occupies; CJK glyphs take two."""
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)


def clip(text, width):
    """Truncate to `width` terminal columns, never splitting a wide glyph."""
    out, used = [], 0
    for c in text:
        step = 2 if unicodedata.east_asian_width(c) in 'WF' else 1
        if used + step > width:
            break
        out.append(c)
        used += step
    return ''.join(out)


def cell(text, width, color=''):
    """Pad to `width` terminal columns; ANSI codes must not count toward it."""
    text = clip(text, width)
    pad = ' ' * max(0, width - disp_width(text))
    return (color + text + OFF if color else text) + pad


def board(state, at, width):
    spark_w = max(20, width - 69)
    lines = ['%s盯盘%s %s HKT   %d 个标的   每 %ds 刷新   Ctrl-C 退出' % (
        BOLD, OFF, at.strftime('%Y-%m-%d %H:%M:%S'), len(state), POLL_SEC), '-' * width]
    lines.append(''.join(cell(h, wd) for h, wd in (
        ('标的', 11), ('名称', 11), ('最新', 9), ('开盘起', 9), ('距VWAP', 8), ('RSI', 5),
        ('量比', 6), ('区间位', 8)))
        + '走势 / 信号 / 成交量   (大写=带量)')
    feed = []
    for code, st in state.items():
        if st.get('error'):
            lines.append(cell(code, 11) + cell(st.get('name', ''), 11) + RED + st['error'] + OFF)
            continue
        bars, ms, d = st['bars'], st['marks'], st['read']
        last = bars[-1][4]
        chg = (last / bars[0][1] - 1) * 100
        dv = (last / d['vwap'] - 1) * 100 if d.get('vwap') else 0.0
        vr = d.get('vol_ratio', 0.0)
        lines.append(
            cell(code, 11) + cell(st.get('name', ''), 11, BOLD)
            + cell('%.2f' % last, 9)
            + cell('%+.2f%%' % chg, 9, GREEN if chg > 0 else RED)
            + cell('%+.2f%%' % dv, 8, GREEN if dv > 0 else RED)
            + cell('-' if d.get('rsi') is None else '%.0f' % d['rsi'], 5)
            + cell('%.1fx' % vr, 6, BOLD if vr >= VOL_MULT else DIM)
            + cell('%.0f%%' % (d.get('pos', 0.5) * 100), 8)
            + spark(bars, spark_w))
        row = mark_row(bars, ms, spark_w)
        lines.append(cell('  信号', 69, BOLD)
                     + (row if row else DIM + '(今日无标注)' + OFF))
        lines.append(cell('  成交量', 69, DIM) + DIM + vol_row(bars, spark_w) + OFF)
        feed += [dict(m, code=code, name=st.get('name', '')) for m in ms]
    vol_ms = [m for m in feed if m['tier'] == 'vol']
    lines += ['-' * width,
              '%s最近标注%s  %s大写=带量+位置好（更少，非更准）  小写=普通%s' % (BOLD, OFF, DIM, OFF)]
    feed.sort(key=lambda m: m['t'])
    if not feed:
        lines.append(DIM + '  (暂无)' + OFF)
    for m in feed[-RECENT_MARKS:]:
        is_vol = m['tier'] == 'vol'
        col = GREEN if m['side'] == 'BUY' else RED
        stop = '' if m['stop'] is None else '  失效 %.2f' % m['stop']
        body = ('  ' + cell(m['t'], 7) + cell(m['code'], 11)
                + cell(m.get('name', ''), 11, BOLD if is_vol else '')
                + cell(m['side'] if is_vol else m['side'].lower(), 6, col)
                + cell(m['trigger'], 12, BOLD if is_vol else '')
                + '@%.2f  量 %.1fx  rsi %.0f  区间位 %.0f%%%s'
                % (m['close'], m['vol_ratio'], m['rsi'], m['pos'] * 100, stop))
        lines.append(body if is_vol else DIM + re.sub(r'\x1b\[[0-9;]*m', '', body) + OFF)
    lines.append('%s今日 带量 %d 个 / 普通 %d 个%s' % (DIM, len(vol_ms), len(feed) - len(vol_ms), OFF))
    return '\n'.join(lines)


def main():
    codes = sys.argv[1:] or DEFAULT_CODES
    deadline = close_hkt()
    ctx = OpenQuoteContext(host=HOST, port=PORT)
    try:
        ret, err = ctx.subscribe(codes, [SubType.K_1M], subscribe_push=False)
        if ret != RET_OK:
            emit('FAILED', {'error': 'subscribe %s' % err})
            return 1
        emit('START', {'codes': codes, 'until': deadline.isoformat(),
                       'poll_sec': POLL_SEC, 'log': str(log_path())})
        seen, fails, state, names = {}, 0, {}, {}
        while True:
            ok = False
            for code in codes:
                bars, name = session_bars(ctx, code)
                if not bars:
                    state[code] = {'error': 'no data', 'name': names.get(code, '')}
                    continue
                ok = True
                names[code] = name or names.get(code, '')
                ms, d = marks(bars)
                state[code] = {'bars': bars, 'marks': ms, 'read': d or read(bars),
                               'name': names[code]}
                for m in ms[seen.get(code, 0):]:
                    emit('MARK', dict(m, code=code, name=names[code]))
                seen[code] = len(ms)
            if ok:
                fails = 0
            else:
                fails += 1
                if fails == STALE_POLLS:
                    emit('STALE', {'failed_polls': fails, 'at': now_hkt().isoformat()})
            if sys.stdout.isatty():
                width = min(140, max(80, shutil.get_terminal_size((100, 24)).columns))
                sys.stdout.write('\033[H\033[J' + board(state, now_hkt(), width) + '\n')
                sys.stdout.flush()
            if now_hkt() >= deadline:
                break
            time.sleep(POLL_SEC)
        emit('DONE', {'at': now_hkt().isoformat(),
                      'marks': {c: len(s.get('marks', [])) for c, s in state.items()}})
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        emit('FAILED', {'error': str(exc)})
        return 1
    finally:
        try:
            ctx.close()
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main())
