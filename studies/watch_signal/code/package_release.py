"""Split the study CSVs into categorised release directories with CHECKSUMS + manifest."""
import csv, gzip, hashlib, json, os, sys
SRC = '/opt/opend-us-options/studies/watch_signal/data'
TODAY = sys.argv[1]
OUT = '/opt/opend-us-options/data'
COLS = ('code', 'time_key', 'open', 'high', 'low', 'close', 'volume', 'turnover', 'last_close')
INDEX = {'HK.800000', 'HK.800700', 'HK.02800'}
def rows(path):
    with gzip.open(path, 'rt') as fh:
        for r in csv.DictReader(fh):
            yield r
def sha(p):
    h = hashlib.sha256(); h.update(open(p, 'rb').read()); return h.hexdigest()
def write(tag, files, meta):
    d = os.path.join(OUT, tag)
    if os.path.exists(d): raise SystemExit('exists: ' + d)
    os.makedirs(d)
    stats = {}
    for name, gen, cols in files:
        p = os.path.join(d, name); n = 0; codes = set(); days = set()
        with gzip.open(p, 'wt', newline='') as fh:
            w = csv.writer(fh); w.writerow(cols)
            for r in gen:
                w.writerow([r.get(c, '') for c in cols]); n += 1; codes.add(r['code']); days.add((r.get('time_key') or r.get('time'))[:10])
        stats[name] = {'rows': n, 'codes': len(codes), 'trading_days': len(days),
                       'first_day': min(days), 'last_day': max(days)}
    meta['files'] = stats
    json.dump(meta, open(os.path.join(d, 'manifest.json'), 'w'), ensure_ascii=False, indent=2)
    with open(os.path.join(d, 'CHECKSUMS.sha256'), 'w') as fh:
        for f in sorted(os.listdir(d)):
            if f != 'CHECKSUMS.sha256': fh.write('%s  %s\n' % (sha(os.path.join(d, f)), f))
    print(tag, stats)
def stocks(lo, hi):
    for f in ('old1m.csv.gz', 'hk1m_long.csv.gz', 'new1m.csv.gz'):
        for r in rows(os.path.join(SRC, f)):
            if r['code'] not in INDEX and lo <= r['time_key'][:10] <= hi: yield r
COMMON = {'source': 'Futu OpenD request_history_kline, K_1M, AuType.QFQ (read-only quotes)',
          'universe': 'HK Stock Connect common stocks already charged in the 30-day history quota (140 names)',
          'bar_notes': 'time_key is bar end (HKT). The 09:30 bar is the opening auction print (O=H=L=C). 16:00 is the closing auction. Lunch 12:00-13:00 has no bars.',
          'study': 'studies/watch_signal (W1-W5)', 'not_custody': 'Not a custody 0DTE dataset; not read by custody check/evaluate.'}
write('watch-hk1m-train-v1', [('bars_1m.csv.gz', stocks('2025-12-01', '2026-08-14'), COLS)],
      dict(COMMON, role='train / selection', window='2025-12-01..2026-08-14'))
write('watch-hk1m-valid-v1', [('bars_1m.csv.gz', stocks('2026-08-17', '2026-09-23'), COLS)],
      dict(COMMON, role='validation (only evaluate, never tune)', window='2026-08-17..2026-09-23'))
def index_rows():
    for r in rows(os.path.join(SRC, 'old1m.csv.gz')):
        if r['code'] == 'HK.800000': yield r
    for r in rows(os.path.join(SRC, 'idx1m.csv.gz')): yield r
write('watch-hk-index1m-v1', [('index_1m.csv.gz', index_rows(), COLS)],
      dict(COMMON, universe='HK.800000 HSI (2025-12-01..2026-09-23), HK.800700 HSTECH and HK.02800 Tracker Fund (2026-06-01..2026-09-23)',
           role='market reference for relative moves', window='2025-12-01..2026-09-23'))
FC = ('code', 'time', 'in_flow', 'super_in_flow', 'big_in_flow', 'mid_in_flow', 'sml_in_flow')
BC = ('code', 'time_key', 'open', 'high', 'low', 'close', 'volume', 'turnover')
write('watch-hk-flow-%s-partial' % TODAY,
      [('capital_flow_1m.csv.gz', rows(os.path.join(TODAY_DIR := sys.argv[2], 'flow_%s.csv.gz' % TODAY)), FC),
       ('bars_1m.csv.gz', rows(os.path.join(TODAY_DIR, 'bars_%s.csv.gz' % TODAY)), BC)],
      dict(COMMON, source='Futu OpenD get_capital_flow(PeriodType.INTRADAY) + request_history_kline K_1M QFQ',
           role='one-day test of the optional flow score (NOT evidence)', window='%s 09:30..13:53 HKT, session still open' % TODAY,
           flow_notes='Cumulative per-minute net buy-initiated turnover (HKD) by order size; in_flow = total. Only the latest session is available from OpenD.'))
