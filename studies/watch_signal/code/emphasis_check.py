"""Report only: which emphasis (fine tpb>=5 vs D+) is better supported across segments."""
import pickle
from balstat import bal, diff
A = pickle.load(open('marks_daily.pkl', 'rb')); _, _, M = pickle.load(open('w9_h2025.pkl', 'rb'))
k5 = lambda m: m['ret5d'] <= 0 and not m['pdlevel']
for name, rows in (('2025-06..11', M), ('selection', [m for m in A if m['seg'] == 'train']), ('validation', [m for m in A if m['seg'] == 'valid'])):
    g = [m for m in rows if m['tpb'] >= 2]
    fine = [m for m in g if m['tpb'] >= 5]; mid = [m for m in g if m['tpb'] < 5]
    print('%-12s fine>=5 close %+6.2f t%+4.1f (n%d) | 2-5 close %+6.2f t%+4.1f | fine-mid diff %+6.2f t%+4.1f | fine&D+ %+6.2f (n%d) fine&D- %+6.2f'
          % (name, *bal(fine, 'fclose')[:2], len(fine), *bal(mid, 'fclose')[:2], *diff(fine, mid, 'fclose'),
             bal([m for m in fine if k5(m)], 'fclose')[0], len([m for m in fine if k5(m)]), bal([m for m in fine if not k5(m)], 'fclose')[0]))
