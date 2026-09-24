"""Report only: W6 K5 across all three segments (selection, validation, 2025H2)."""
import pickle
from balstat import bal, diff, side_mean
A = pickle.load(open('marks_daily.pkl', 'rb'))
_, _, M = pickle.load(open('w9_h2025.pkl', 'rb'))
k5 = lambda m: m['ret5d'] <= 0 and not m['pdlevel']
segs = {'2025-06..11 (new)': [m for m in M], '2025-12..2026-08-14 (selection)': [m for m in A if m['seg'] == 'train'],
        '2026-08-17..09-23 (validation)': [m for m in A if m['seg'] == 'valid']}
segs['ALL three'] = sum(segs.values(), [])
for gran in (True, False):
    print('==', 'granular' if gran else 'coarse ~')
    for name, rows in segs.items():
        rows = [m for m in rows if (m['tpb'] >= 2) == gran]
        yes = [m for m in rows if k5(m)]; no = [m for m in rows if not k5(m)]
        y, n, d, d30 = bal(yes, 'fclose'), bal(no, 'fclose'), diff(yes, no, 'fclose'), diff(yes, no, 'f30')
        print('  %-32s days %3d | D+ %+6.2f t%+4.1f | D- %+6.2f t%+4.1f | diff %+6.2f t%+4.1f | f30 diff %+5.2f t%+4.1f | BUY %+6.2f/%+6.2f SELL %+6.2f/%+6.2f'
              % (name, len({m['day'] for m in rows}), y[0], y[1], n[0], n[1], d[0], d[1], d30[0], d30[1],
                 side_mean(yes, 1, 'fclose')[0], side_mean(no, 1, 'fclose')[0], side_mean(yes, -1, 'fclose')[0], side_mean(no, -1, 'fclose')[0]))
print('== out-of-selection only (validation + 2025H2), granular')
rows = [m for m in segs['2025-06..11 (new)'] + segs['2026-08-17..09-23 (validation)'] if m['tpb'] >= 2]
yes = [m for m in rows if k5(m)]; no = [m for m in rows if not k5(m)]
y, n, d, d30 = bal(yes, 'fclose'), bal(no, 'fclose'), diff(yes, no, 'fclose'), diff(yes, no, 'f30')
print('  days %d | D+ %+6.2f t%+4.1f | D- %+6.2f t%+4.1f | diff %+6.2f t%+4.1f | f30 diff %+5.2f t%+4.1f' % (len({m['day'] for m in rows}), y[0], y[1], n[0], n[1], d[0], d[1], d30[0], d30[1]))
