#!/usr/bin/env python3
"""Intraday tape watcher: VWAP + EMA9/21 + RSI14 bias, SMC structure triggers.

Bias (unchanged from the original): price vs session VWAP, EMA9/21, RSI14.
Triggers:
    BOS    break of structure through a swing confirmed on BOTH sides
    FVG    3-bar fair value gap wider than FVG_ATR x ATR14
    SWEEP  wick takes out a confirmed swing then closes back inside (stop hunt)

Every trigger that passes the bias is marked. A session averaging fewer than
GATE_TPB ticks per bar turns many "breaks" into bid/ask flips; those marks
leaned slightly the wrong way out of sample (study W2, studies/watch_signal/),
so they are shown dim and tagged `~` rather than hidden. Each mark also gets a
daily-context tag, D+ or D- (study W6), and only granular D+ marks are drawn
bold. Volume expansion and the premium/discount half of the day's range only
split `plain` from `vol` marks for de-duplication.

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
import urllib.request
from datetime import datetime, timedelta, timezone
import unicodedata
from pathlib import Path

logging.getLogger('futu').setLevel(logging.ERROR)
logging.getLogger().setLevel(logging.ERROR)

from futu import AuType, KLType, OpenQuoteContext, RET_OK, SubType  # noqa: E402

# futu prints connection notices to stdout through its own logger, which would
# break the one-JSON-object-per-line output that `| jq` relies on.
logging.getLogger('FTConsoleLog').setLevel(logging.ERROR)

DEFAULT_CODES = ['HK.02513']
HOST = '127.0.0.1'
PORT = 11111
POLL_SEC = 30
KLINE_BACK = 400        # > one HK session of 1m bars
TENCENT_M1 = 'https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=%s,m1,,320'
TENCENT_DAY = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,12,qfq'
DAILY_BACK = 12         # daily bars requested; the tag needs the last 6 closed sessions
SWING_LEFT = 3          # bars confirmed on BOTH sides of a swing
VOL_MULT = 1.5          # trigger bar volume vs the trailing 20-bar mean
VOL_LOOKBACK = 20
FVG_ATR = 0.25          # gap must be this fraction of ATR14 to count
ATR_LEN = 14
PREMIUM = 0.5           # longs below this much of the day's range, shorts above
WARMUP_BARS = 25
DEDUP_BARS = 15         # per direction
GATE_TPB = 2.0          # below this many ticks per bar a "break" is a bid/ask flip
FINE_TPB = 5.0          # `fine` flag in the log; no longer decides emphasis (W9)
HK_TICK_BANDS = ((0.25, 0.001), (0.5, 0.005), (10, 0.01), (20, 0.02), (100, 0.05),
                 (200, 0.1), (500, 0.2), (1000, 0.5), (2000, 1.0), (5000, 2.0))
STALE_POLLS = 4
LAST_BAR = ' 16:00'     # the 16:00 bar is the closing auction, not continuous trade
HKT = timezone(timedelta(hours=8))
LOG_DIR = Path(__file__).resolve().parent / 'logs'

DIM, BOLD, OFF = '\033[2m', '\033[1m', '\033[0m'
GREEN, RED = '\033[32m', '\033[31m'


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
    elif kind == 'FAILED':  # the board never shows it, so say why we exit
        print('FAILED: %s' % payload.get('error'), file=sys.stderr, flush=True)


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


def closed_session(bars):
    """Today's finished 1-minute bars. The bar still forming is dropped."""
    now = now_hkt()
    day = now.strftime('%Y-%m-%d')
    out = [b for b in bars if day <= b[0] < day + LAST_BAR]
    if out and out[-1][0].startswith(now.strftime('%Y-%m-%d %H:%M')):
        out.pop()
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
    return closed_session(frame_bars(frame)), frame_name(frame)


def daily_context(rows, day):
    """Pre-open daily context from sessions strictly before `day`.

    rows: [(YYYY-MM-DD, open, high, low, close, volume)], any order. Returns
    None when fewer than six closed sessions are available.
    """
    prev = sorted(r for r in rows if r[0] < day)
    if len(prev) < 6:
        return None
    return {'ret5d': prev[-1][4] / prev[-6][4] - 1.0, 'pdh': prev[-1][2],
            'pdl': prev[-1][3], 'day': prev[-1][0]}


def daily_support(m, dctx):
    """Study W6 (K5): the mark goes against the last five sessions AND has not
    broken the previous session's high (BUY) / low (SELL). None without data.

    Granular marks held to the close, D+ minus D-: +11.5bp (t=2.0) over the
    two segments not used for selection (2025-06..11 and 2026-08-17..09-23),
    with BUY and SELL agreeing in every segment. Below round-trip cost.
    """
    if not dctx:
        return None
    if m['side'] == 'BUY':
        return dctx['ret5d'] <= 0 and m['close'] <= dctx['pdh']
    return dctx['ret5d'] >= 0 and m['close'] >= dctx['pdl']


def with_daily(ms, dctx):
    return [dict(m, daily=daily_support(m, dctx)) for m in ms]


def hk_daily(ctx, code):
    """Daily bars from OpenD (subscription quota only, never history quota)."""
    try:
        ret, frame = ctx.get_cur_kline(code, DAILY_BACK, KLType.K_DAY, AuType.QFQ)
    except Exception:
        return None
    if ret != RET_OK:
        return None
    return [(b[0][:10],) + b[1:] for b in frame_bars(frame)]


def parse_tencent_day(payload, symbol):
    """Tencent daily row is date, open, close, high, low, volume."""
    node = (payload.get('data') or {}).get(symbol) or {}
    out = []
    for row in node.get('qfqday') or node.get('day') or []:
        try:
            out.append((str(row[0]), float(row[1]), float(row[3]), float(row[4]), float(row[2]), float(row[5] or 0)))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def tencent_daily(code):
    symbol = tencent_symbol(code)
    if not symbol:
        return None
    try:
        req = urllib.request.Request(TENCENT_DAY % symbol, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=12) as resp:
            return parse_tencent_day(json.loads(resp.read().decode('utf-8')), symbol) or None
    except Exception:
        return None


def tencent_symbol(code):
    """Futu-style or bare A-share code -> sz300795. HK codes return None."""
    raw = code.strip().upper().replace(' ', '')
    if raw.startswith('HK.') or not raw:
        return None
    prefix = {'SH': 'sh', 'SZ': 'sz', 'BJ': 'bj'}
    if '.' in raw:
        left, right = raw.split('.', 1)
        if left in prefix and right.isdigit() and len(right) == 6:
            return prefix[left] + right
        if right in prefix and left.isdigit() and len(left) == 6:
            return prefix[right] + left
        return None
    low = code.strip().lower()
    if len(low) == 8 and low[:2] in ('sh', 'sz', 'bj') and low[2:].isdigit():
        return low
    if len(raw) == 6 and raw.isdigit():
        head = {'6': 'sh', '0': 'sz', '3': 'sz', '4': 'bj', '8': 'bj'}.get(raw[0])
        return head + raw if head else None
    return None


def canonical_code(code):
    code = code.encode('ascii', 'ignore').decode()  # pasted args can carry stray bytes
    symbol = tencent_symbol(code)
    if not symbol:
        return code.strip()
    return '%s.%s' % (symbol[:2].upper(), symbol[2:])


def parse_tencent_m1(payload, symbol):
    """Tencent m1 row is time, open, close, high, low, volume."""
    node = (payload.get('data') or {}).get(symbol) or {}
    qt = (node.get('qt') or {}).get(symbol) or []
    name = str(qt[1]) if len(qt) > 1 else ''
    bars = []
    for row in node.get('m1') or []:
        if not row or len(row) < 6:
            continue
        stamp = str(row[0])
        if len(stamp) != 12 or not stamp.isdigit():
            continue
        try:
            o, c, h, l, v = (float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5] or 0))
        except (TypeError, ValueError):
            continue
        if not all(x == x for x in (o, h, l, c, v)):
            continue
        bars.append(('%s-%s-%s %s:%s:00' % (stamp[0:4], stamp[4:6], stamp[6:8], stamp[8:10], stamp[10:12]),
                     o, h, l, c, v))
    return bars, name


def fetch_tencent_m1(symbol):
    req = urllib.request.Request(TENCENT_M1 % symbol, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    if payload.get('code') not in (0, None):
        return [], ''
    return parse_tencent_m1(payload, symbol)


def tencent_session(code):
    symbol = tencent_symbol(code)
    if not symbol:
        return None, ''
    try:
        bars, name = fetch_tencent_m1(symbol)
    except Exception:
        return None, ''
    bars = closed_session(bars)
    return (bars, name) if bars else (None, name)


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


def hk_tick(price):
    """HKEX spread table (the part that covers listed equities)."""
    for upper, tick in HK_TICK_BANDS:
        if price < upper:
            return tick
    return 5.0


def a_tick(_price):
    return 0.01


def ticks_per_bar(bars, tick=hk_tick):
    """Session-to-date mean bar range (high - low) in ticks, one value per bar.

    Bar 0 is the opening auction print (O=H=L=C), so it is left out. Causal:
    entry i only uses bars 0..i, which keeps a restart reproducible.
    """
    out, total = [], 0.0
    for i, b in enumerate(bars):
        if i:
            step = tick(b[4])
            total += (b[2] - b[3]) / step if step > 0 else 0.0
        out.append(total / max(i, 1))
    return out


def _emit_ok(rule, i, side, tier, d, last, anchor):
    """Whether this bar may become a mark. Indicators are already computed.

    repeat15  current rule: same side and tier, quiet for 15 bars.
    repeat30  the same quiet stretch, 30 bars.
    episode   one mark per side until the opposite side prints.
    extend2   repeat15, and drop a same-side mark once price has run more
              than 2x ATR past the close of the last emitted mark of that side.
    """
    if rule == 'episode':
        return not (anchor and anchor[0] == side)
    if rule == 'extend2' and anchor and anchor[0] == side:
        moved = (d['close'] - anchor[1]) if side == 'BUY' else (anchor[1] - d['close'])
        if d['atr'] > 0 and moved > 2.0 * d['atr']:
            return False
    gap = 30 if rule == 'repeat30' else DEDUP_BARS
    return i - last.get((side, tier), -10 ** 9) >= gap


def marks(bars, rule='repeat15', tick=hk_tick):
    """Two tiers of marks, derived purely from `bars`.

    plain  bias + structure trigger.
    vol    the same trigger with volume expansion AND in the right half of the
           day's range.

    `rule` only changes emission. Bias stays VWAP / EMA9 / EMA21 / RSI14, and
    triggers stay BOS / FVG / SWEEP. The live default is `repeat15`: on
    HK.02513 / HK.09988 / HK.01810 / HK.00100 from 2026-07-28 through
    2026-09-22 it had the best second-half 30-bar mean among repeat15,
    repeat30, episode, and extend2, and none of the others raised that
    half without giving up the pooled mean. `vol` marks are NOT more
    accurate than `plain` ones on the older 40-day cut -- only rarer.

    Tick density (study W2, studies/watch_signal/): a mark whose bar sits in
    a session averaging fewer than GATE_TPB ticks per bar is `coarse`. There
    a break is often the close flipping from bid to ask; those marks averaged
    -1.88bp out of sample (t = -6.6), about 0.2 tick -- real but small, so
    they are downgraded on the board, not dropped. `fine` (>= FINE_TPB ticks
    per bar) is logged only: on its own it was never significant (W9).
    """
    if rule not in ('repeat15', 'repeat30', 'episode', 'extend2'):
        raise ValueError('unknown mark rule %r' % rule)
    out, last, anchor, d = [], {}, None, None
    tpb = ticks_per_bar(bars, tick)
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
        if not _emit_ok(rule, i, side, tier, d, last, anchor):
            continue
        last[(side, tier)] = i
        anchor = (side, d['close'])
        out.append({'i': i, 't': bars[i][0][11:16], 'side': side, 'tier': tier,
                    'tpb': round(tpb[i], 2), 'fine': tpb[i] >= FINE_TPB,
                    'coarse': tpb[i] < GATE_TPB,
                    'trigger': trig, 'close': d['close'], 'rsi': round(d['rsi'], 1),
                    'vwap': round(d['vwap'], 3), 'vol_ratio': round(d['vol_ratio'], 2),
                    'pos': round(d['pos'], 2),
                    'stop': d['swing_low'] if side == 'BUY' else d['swing_high']})
    if d is not None and tpb:
        d['tpb'] = tpb[-1]
    return out, d


def is_strong(m):
    return m.get('daily') is True and not m.get('coarse')


def paint_side(side, strong):
    """Strong marks (granular tape and D+) are bold B/S, the rest dim b/s."""
    ch = 'B' if side == 'BUY' else 'S'
    if not strong:
        ch = ch.lower()
    color = GREEN if side == 'BUY' else RED
    return color + (BOLD if strong else '') + ch + OFF


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


def mark_text(m, width):
    """One signal. Bold = granular tape AND daily D+ (the best-supported group
    across three independent segments, studies W2/W6/W9), `~` = tick-bound."""
    strong = is_strong(m)
    inv = '-' if m.get('stop') is None else '%.2f' % m['stop']
    rest = clip(('~' if m.get('coarse') else ' ') + cell(m['trigger'], 13) + cell('%.2f' % m['close'], 9)
                + cell('%.1fx' % m['vol_ratio'], 6)
                + cell({True: 'D+', False: 'D-'}.get(m.get('daily'), ''), 3)
                + 'inv ' + inv, width - 9)
    base = '' if strong else DIM
    return base + '  ' + cell(m['t'], 6) + paint_side(m['side'], strong) + base + rest + OFF


def symbol_lines(code, st, width):
    """One symbol's quote and every signal, oldest first."""
    name = st.get('name', '')
    if st.get('error'):
        return [cell(code, 11) + cell(name, 10, DIM) + DIM + st['error'] + OFF]
    bars, ms, d = st['bars'], st['marks'], st['read']
    last = bars[-1][4]
    op = bars[0][1] or last
    ret_bp = (last / op - 1.0) * 10000.0
    vwap_bp = (last / d['vwap'] - 1.0) * 10000.0 if d.get('vwap') else 0.0
    rsi = d.get('rsi')
    vr = d.get('vol_ratio', 0.0)
    tpb = d.get('tpb')
    lines = [
        cell(code, 11) + cell(name, 10, DIM)
        + cell('%.2f' % last, 9)
        + cell('%+dbp' % int(round(ret_bp)), 8)
        + cell('vwap%+d' % int(round(vwap_bp)), 9)
        + cell('rsi%s' % ('-' if rsi is None else '%.1f' % rsi), 8)
        + cell('%.1fx' % vr, 6, BOLD if vr >= VOL_MULT else DIM)
        + DIM + ' tpb%s n%d' % ('-' if tpb is None else '%.1f' % tpb, len(bars)) + OFF]
    if not ms:
        lines.append(DIM + '  (none)' + OFF)
    for m in ms:
        lines.append(mark_text(m, width))
    return lines


def _plain_width(text):
    return disp_width(re.sub(r'\x1b\[[0-9;]*m', '', text))


def _pad(text, width):
    gap = width - _plain_width(text)
    return text + (' ' * gap) if gap > 0 else text


def _stack(blocks):
    lines = []
    for i, block in enumerate(blocks):
        if i:
            lines.append('')
        lines.extend(block)
    return lines


def _columns(blocks, width):
    """Two columns of whole symbol blocks. A block is never split."""
    col_w = (width - 1) // 2
    left, right, hl, hr = [], [], 0, 0
    for block in blocks:
        if hl <= hr:
            if left:
                left.append('')
                hl += 1
            left.extend(block)
            hl += len(block)
        else:
            if right:
                right.append('')
                hr += 1
            right.extend(block)
            hr += len(block)
    lines = []
    for i in range(max(len(left), len(right))):
        a = left[i] if i < len(left) else ''
        b = right[i] if i < len(right) else ''
        lines.append(_pad(a, col_w) + ' ' + b)
    return lines


def frame_lines(state, at, width, rows=None):
    """Grouped log. Two columns only when that keeps every signal on screen."""
    head = 'kline.1m %s+08 poll=%ds n=%d until=16:10' % (
        at.strftime('%Y-%m-%d %H:%M:%S'), POLL_SEC, len(state))
    if width >= 100:
        head += ' src=%s:%s' % (HOST, PORT)
    legend = (GREEN + 'B' + OFF + '/' + RED + 'S' + OFF
              + DIM + ' D+ & >=%g ticks/bar  ' % GATE_TPB + OFF
              + GREEN + 'b' + OFF + '/' + RED + 's' + OFF
              + DIM + ' plain  ~ <%g ticks  D+/D- daily   ^C' % GATE_TPB + OFF)
    chrome = [BOLD + clip(head, width) + OFF, legend, '-' * width]
    blocks = [symbol_lines(code, st, width) for code, st in state.items()]
    body = _stack(blocks)
    room = None if rows is None else rows - 1 - len(chrome)
    if room is not None and len(body) > room and width >= 136:
        col_w = (width - 1) // 2
        narrow = [symbol_lines(code, st, col_w) for code, st in state.items()]
        side = _columns(narrow, width)
        if len(side) <= room:
            body = side
    return chrome + body


def board(state, at, width):
    """One log per symbol. No price chart."""
    return '\n'.join(frame_lines(state, at, width))


def paint(lines, prev, rows):
    """Redraw the grouped log. Unchanged lines stay put.

    A new signal changes the length, so the whole frame is rewritten with
    that signal still inside its own symbol block. Absolute row updates are
    only used when the frame fits; a taller frame is written in full so
    nothing is dropped.
    """
    title = '\033]0;kline.1m\007'
    if prev is None or len(lines) != len(prev) or len(lines) > rows - 1:
        sys.stdout.write('\033[H\033[J' + title + '\n'.join(lines) + '\n')
    else:
        parts = [title]
        for i, (old, new) in enumerate(zip(prev, lines)):
            if old != new:
                parts.append('\033[%d;1H\033[2K%s' % (i + 1, new))
        if len(parts) > 1:
            sys.stdout.write(''.join(parts))
    sys.stdout.flush()
    return lines


def main():
    codes = [canonical_code(c) for c in (sys.argv[1:] or DEFAULT_CODES)]
    deadline = close_hkt()
    hk_codes = [c for c in codes if not tencent_symbol(c)]
    ctx = None
    try:
        if hk_codes:
            ctx = OpenQuoteContext(host=HOST, port=PORT)
            ret, err = ctx.subscribe(hk_codes, [SubType.K_1M], subscribe_push=False)
            if ret != RET_OK:
                emit('FAILED', {'error': 'subscribe %s' % err})
                return 1
            # separate call: a failed K_DAY subscription only costs the D+/D- tag
            ctx.subscribe(hk_codes, [SubType.K_DAY], subscribe_push=False)
        emit('START', {'codes': codes, 'until': deadline.isoformat(),
                       'poll_sec': POLL_SEC, 'log': str(log_path())})
        seen, fails, state, names, prev, dctxs = {}, 0, {}, {}, None, {}
        while True:
            ok = False
            for code in codes:
                if tencent_symbol(code):
                    bars, name = tencent_session(code)
                else:
                    bars, name = session_bars(ctx, code)
                if not bars:
                    state[code] = {'error': 'no data', 'name': names.get(code, '')}
                    continue
                ok = True
                names[code] = name or names.get(code, '')
                ms, d = marks(bars, tick=a_tick if tencent_symbol(code) else hk_tick)
                today = bars[-1][0][:10]
                if dctxs.get(code, (None,))[0] != today:
                    rows = tencent_daily(code) if tencent_symbol(code) else hk_daily(ctx, code)
                    dc = daily_context(rows or [], today)
                    if dc:
                        dctxs[code] = (today, dc)
                ms = with_daily(ms, dctxs.get(code, (None, None))[1])
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
                size = shutil.get_terminal_size((100, 40))
                width = min(140, max(80, size.columns))
                prev = paint(frame_lines(state, now_hkt(), width, size.lines), prev, size.lines)
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
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == '__main__':
    sys.exit(main())
