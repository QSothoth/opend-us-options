#!/usr/bin/env python3
"""Self-check for the watcher. Runs without futu: `python3 watch/test_watch.py`."""
import importlib.util
import re
import sys
import types

_futu = types.ModuleType('futu')
_futu.AuType = _futu.KLType = _futu.SubType = type('X', (), {'K_1M': None, 'NONE': None})
_futu.OpenQuoteContext = object
_futu.RET_OK = 0
sys.modules.setdefault('futu', _futu)

_spec = importlib.util.spec_from_file_location(
    'watcher', __file__.replace('test_watch.py', 'smc_watch.py'))
w = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(w)

NAN = float('nan')


class Frame:
    def __init__(self, rows, name='腾讯控股'):
        self.rows = [(name,) + r for r in rows]
        self.columns = ['name', 'time_key', 'open', 'high', 'low', 'close', 'volume']

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, col):
        return [r[self.columns.index(col)] for r in self.rows]


def stamp(i):
    return '2026-09-21 %02d:%02d:00' % (9 + (30 + i) // 60, (30 + i) % 60)


def fake_bars():
    """Hand-built day: an opening wick sets the high (closes untouched, so the
    EMAs stay low), then a quiet base, then one volume-expansion break of
    structure that lands in the lower part of the range."""
    rows = [(stamp(0), 96.0, 103.0, 95.5, 96.0, 5000.0)]
    closes = ([96.0] * 4 + [95.7, 95.5, 95.6, 95.8, 96.1, 95.9, 95.6, 95.5]
              + [95.6, 95.7, 95.8, 95.9, 96.0, 96.05, 96.1, 96.05]
              + [95.9, 95.8, 95.85, 95.95, 96.05, 96.15, 96.2, 96.25]
              + [96.3, 96.35, 96.4, 96.45])
    for c in closes:
        o = rows[-1][4]
        rows.append((stamp(len(rows)), o, max(o, c) + 0.05, min(o, c) - 0.05, c, 200000.0))
    rows.append((stamp(len(rows)), rows[-1][4], 97.5, rows[-1][4] - 0.05, 97.4, 900000.0))
    for k in range(10):
        o, c = rows[-1][4], 97.4 + 0.01 * k
        rows.append((stamp(len(rows)), o, max(o, c) + 0.05, min(o, c) - 0.05, c, 200000.0))
    return rows


def hot(**kw):
    """An indicator snapshot that already clears the volume gate."""
    d = {'vol_ratio': w.VOL_MULT, 'atr': 1.0, 'close': 100.0,
         'swing_high': None, 'swing_low': None}
    d.update(kw)
    return d


def test_rsi_matches_wilder_on_a_known_series():
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
              45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert abs(w.rsi_last(closes) - 70.46) < 0.5, w.rsi_last(closes)


def test_swing_needs_both_sides_confirmed():
    # pullback to 10.9, then a breakout close above it -> BOS must be reachable
    highs = [10, 10.2, 10.1, 10.3, 10.25, 10.9, 10.6, 10.5, 10.4, 10.45, 10.55, 10.95]
    lows = [h - 0.2 for h in highs]
    sh, _sl = w.last_swing(highs, lows)
    assert sh == 10.9, sh
    assert 10.92 > sh  # the original bug returned 10.95 and BOS could never fire


def test_swing_ignores_unconfirmed_right_edge():
    highs = [10, 10.2, 10.1, 10.3, 10.25, 10.4, 10.35, 10.5, 10.45, 10.6, 10.55, 11.0]
    lows = [h - 0.2 for h in highs]
    assert w.last_swing(highs, lows)[0] != 11.0, 'last bar must not be its own swing high'


def test_volume_only_decides_the_tier_not_the_trigger():
    """structure() must stay volume-blind; marks() is what splits vol/plain."""
    bars = [(stamp(i), 100, 101, 99, 100, 1.0) for i in range(5)]
    quiet = hot(vol_ratio=0.1, close=110.0, swing_high=100.0)
    assert 'BOS' in w.structure(bars, quiet)[0]
    assert w.structure(bars, hot(atr=0.0, close=110.0, swing_high=100.0)) == ([], [])


def test_structure_detects_bos_fvg_and_sweep():
    flat = [(stamp(i), 100, 101, 99, 100, 1.0) for i in range(3)]
    assert 'BOS' in w.structure(flat, hot(close=105.0, swing_high=100.0))[0]
    assert 'BOS' in w.structure(flat, hot(close=95.0, swing_low=100.0))[1]
    gap = [(stamp(0), 100, 101, 99, 100, 1.0), (stamp(1), 101, 104, 101, 103, 1.0),
           (stamp(2), 103, 105, 102, 104, 1.0)]          # low[-1] 102 > high[-3] 101
    assert 'FVG' in w.structure(gap, hot(close=104.0))[0]
    wick = [(stamp(i), 100, 101, 99, 100, 1.0) for i in range(2)]
    wick.append((stamp(2), 100, 101, 94.0, 99.0, 1.0))   # pierces 95 then closes back
    assert 'SWEEP' in w.structure(wick, hot(close=99.0, swing_low=95.0))[0]


def test_frame_bars_drops_nan():
    f = Frame([
        ('2026-09-21 09:30:00', 1.0, 1.5, 0.9, 1.2, 100.0),
        ('2026-09-21 09:31:00', 1.2, NAN, 1.1, 1.3, 100.0),
        ('2026-09-21 09:32:00', 1.3, 1.6, 1.2, 1.4, NAN),
    ])
    assert [b[0][-8:] for b in w.frame_bars(f)] == ['09:30:00']


def test_session_bars_keeps_today_and_drops_the_auction():
    day = w.now_hkt().strftime('%Y-%m-%d')
    f = Frame([
        ('2026-09-18 15:00:00', 1, 1, 1, 1, 10),   # previous session
        (day + ' 09:30:00', 1, 1, 1, 1, 10),
        (day + ' 15:59:00', 1, 1, 1, 1, 10),
        (day + ' 16:00:00', 1, 1, 1, 1, 10),       # closing auction
    ])
    ctx = types.SimpleNamespace(get_cur_kline=lambda *a: (0, f))
    bars, name = w.session_bars(ctx, 'X')
    assert [b[0] for b in bars] == [day + ' 09:30:00', day + ' 15:59:00']
    assert name == '腾讯控股', name
    assert w.session_bars(types.SimpleNamespace(get_cur_kline=lambda *a: (-1, 'x')), 'X')[0] is None


def test_session_bars_drops_the_minute_still_forming():
    """OpenD hands back the in-progress bar; a mark fired on it would be emitted
    and then stop being true once the minute completes."""
    now = w.now_hkt()
    day = now.strftime('%Y-%m-%d')
    live = now.strftime('%Y-%m-%d %H:%M:00')
    f = Frame([(day + ' 09:30:00', 1, 1, 1, 1, 10), (live, 1, 1, 1, 1, 10)])
    ctx = types.SimpleNamespace(get_cur_kline=lambda *a: (0, f))
    assert [b[0] for b in w.session_bars(ctx, 'X')[0]] == [day + ' 09:30:00']


def test_tencent_m1_maps_ohlc_and_a_share_codes():
    assert w.tencent_symbol('SZ.300795') == 'sz300795'
    assert w.tencent_symbol('300795.SZ') == 'sz300795'
    assert w.tencent_symbol('300795') == 'sz300795'
    assert w.tencent_symbol('SH.600519') == 'sh600519'
    assert w.tencent_symbol('HK.02513') is None
    assert w.canonical_code('sz300795') == 'SZ.300795'
    payload = {'code': 0, 'data': {'sz300795': {
        'qt': {'sz300795': ['51', '米奥会展', '300795']},
        'm1': [['202609221110', '14.09', '14.20', '14.25', '14.08', '896.00', {}, '5.14']],
    }}}
    bars, name = w.parse_tencent_m1(payload, 'sz300795')
    assert name == '米奥会展'
    assert bars == [('2026-09-22 11:10:00', 14.09, 14.25, 14.08, 14.20, 896.0)]


def test_closed_session_drops_yesterday_and_the_live_minute():
    now = w.now_hkt()
    day = now.strftime('%Y-%m-%d')
    live = now.strftime('%Y-%m-%d %H:%M:00')
    bars = [
        ('2020-01-01 10:00:00', 1, 1, 1, 1, 1),
        (day + ' 09:30:00', 1, 1, 1, 1, 1),
        (live, 2, 2, 2, 2, 2),
    ]
    assert [b[0] for b in w.closed_session(bars)] == [day + ' 09:30:00']


def test_watch_never_touches_the_trade_api():
    """watch/ is quote-only; custody's own scan does not reach this directory."""
    import ast as _ast
    import pathlib
    forbidden = {'OpenSecTradeContext', 'place_order', 'unlock_trade', 'modify_order'}
    for path in sorted(pathlib.Path(__file__).resolve().parent.glob('*.py')):
        used = set()
        for node in _ast.walk(_ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, _ast.Attribute):
                used.add(node.attr)
            elif isinstance(node, _ast.Name):
                used.add(node.id)
            elif isinstance(node, (_ast.Import, _ast.ImportFrom)):
                used.update(a.name for a in node.names)
        assert not (used & forbidden), (path.name, used & forbidden)


def test_the_fixture_breakout_is_marked_as_volume_confirmed():
    """Guards the whole chain: without this a silent regression (the original
    BOS bug) would pass every other test."""
    ms, _d = w.marks(fake_bars())
    vol = [m for m in ms if m['tier'] == 'vol']
    assert len(vol) == 1, ms
    m = vol[0]
    assert m['side'] == 'BUY' and m['trigger'] == 'BOS', m
    assert m['vol_ratio'] >= w.VOL_MULT and m['pos'] <= w.PREMIUM, m
    assert m['stop'] is not None and m['stop'] < m['close'], m


def test_flat_volume_leaves_only_plain_marks():
    bars = [(t, o, h, l, c, 10000.0) for t, o, h, l, c, _v in fake_bars()]
    ms = w.marks(bars)[0]
    assert ms, 'plain marks must survive without volume expansion'
    assert {m['tier'] for m in ms} == {'plain'}, ms


def test_same_side_same_tier_waits_fifteen_bars():
    """A steady decline keeps reprinting the same sell. The shipped rule
    drops the reprints inside 15 bars and allows the next one after the gap."""
    def stamp(i):
        return '2026-09-21 %02d:%02d:00' % (9 + (30 + i) // 60, (30 + i) % 60)

    px = 100.0
    bars = []
    for i in range(90):
        px -= 0.15
        bars.append((stamp(i), px + 0.05, px + 0.1, px - 0.1, px, 1000.0))
    ms, _d = w.marks(bars)
    idxs = [m['i'] for m in ms if m['side'] == 'SELL' and m['tier'] == 'plain']
    assert idxs[0] == 25 and 40 in idxs, idxs
    assert all(b - a >= 15 for a, b in zip(idxs, idxs[1:]))
    assert not any(i in idxs for i in range(26, 40))


def test_no_marks_during_the_opening_warmup():
    ms, _d = w.marks(fake_bars())
    assert min(m['i'] for m in ms) >= w.WARMUP_BARS


def test_restart_midday_reproduces_the_same_list():
    """Stateless by construction: marks() must be a pure function of the bars."""
    bars = fake_bars()
    full, _d = w.marks(bars)
    for cut in (30, 36, 40):
        part, _d = w.marks(bars[:cut])
        assert part == [m for m in full if m['i'] < cut], 'cut=%d diverges' % cut


def test_vol_tier_keeps_the_old_emphasis():
    """High rv without the vol tier stays a dim lowercase mark."""
    loud = w.mark_text({'t': '10:47', 'side': 'BUY', 'trigger': 'BOS+FVG',
                        'close': 78.95, 'vol_ratio': 3.8, 'stop': 77.1,
                        'tier': 'vol'}, 100)
    quiet = w.mark_text({'t': '10:47', 'side': 'BUY', 'trigger': 'BOS+FVG',
                         'close': 78.95, 'vol_ratio': 3.8, 'stop': 77.1,
                         'tier': 'plain'}, 100)
    assert w.GREEN + w.BOLD + 'B' in loud
    assert w.GREEN + 'b' in quiet and w.BOLD + 'b' not in quiet
    assert quiet.startswith(w.DIM)


def test_board_renders_without_crashing():
    bars = fake_bars()
    ms, d = w.marks(bars)
    out = w.board({'HK.00001': {'bars': bars, 'marks': ms, 'read': d},
                   'HK.00002': {'error': 'no data'}}, w.now_hkt(), 100)
    plain = re.sub(r'\x1b\[[0-9;]*m', '', out)
    assert 'HK.00001' in plain and 'no data' in plain
    assert 'earlier' not in plain
    for m in ms:
        assert m['t'] in plain
    assert 'kline.1m' in plain and 'evt.log' not in plain and 'x' in plain
    assert '盯盘' not in plain and 'BUY' not in plain and 'SELL' not in plain
    assert '┌' not in plain and '█' not in plain
    both = w.board({'HK.00001': {'bars': bars, 'marks': ms, 'read': d, 'name': '甲'},
                    'HK.00002': {'bars': bars, 'marks': ms, 'read': d, 'name': '乙'}},
                   w.now_hkt(), 100)
    both = re.sub(r'\x1b\[[0-9;]*m', '', both)
    head, tail = both.split('HK.00002', 1)
    assert 'HK.00001' in head and re.search(r'\d\d:\d\d', head)
    assert re.search(r'\d\d:\d\d', tail)
    assert '\033[32m' in out and '\033[31m' in out
    for wide in (80, 100):
        rendered = re.sub(r'\x1b\[[0-9;]*m', '', w.board(
            {'HK.00001': {'bars': bars, 'marks': ms, 'read': d, 'name': '腾讯控股'}},
            w.now_hkt(), wide))
        for line in rendered.splitlines():
            assert w.disp_width(line) <= wide, (wide, w.disp_width(line), line)


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for t in tests:
        t()
        print('ok', t.__name__)
    print('%d passed' % len(tests))
