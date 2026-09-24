#!/usr/bin/env python3
"""Pull 60 sessions of 5m bars for the 522 HK Connect names OpenD never charged for.

Yahoo, keyless, no OpenD quota touched.  Writes one gzipped csv plus a manifest
recording what came back, so the backtest can state its own coverage honestly.
"""
import csv, gzip, json, random, sys, time, urllib.error, urllib.request, datetime
from collections import defaultdict

OUT = '/tmp/claude-0/-opt-opend-us-options/d8c681a4-9a79-4bc6-b926-a7c7b297957c/scratchpad/'
SP = '/tmp/claude-0/-opt-opend-us-options/7723de8d-a5a3-4cd6-aced-8a5248bfed6c/scratchpad/'
HKT = datetime.timezone(datetime.timedelta(hours=8))

uni = json.load(open('/opt/opend-us-options-data/hk_open_scan/universe.json'))
charged = set()
with gzip.open(SP + 'hk1m_long.csv.gz', 'rt') as fh:
    for r in csv.DictReader(fh):
        charged.add(r['code'])
targets = [c for c in sorted(uni) if c not in charged]
print('targets: %d mid/small-cap names' % len(targets), flush=True)


def symbol(code):
    n = code.split('.')[1].lstrip('0') or '0'
    return (n.zfill(4) if len(n) <= 4 else n) + '.HK'


def fetch(code, tries=3):
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/%s'
           '?interval=5m&range=60d' % symbol(code))
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            d = json.load(urllib.request.urlopen(req, timeout=30))
            res = (d.get('chart') or {}).get('result')
            if not res:
                return []
            r = res[0]
            ts = r.get('timestamp') or []
            q = r['indicators']['quote'][0]
            rows = []
            for i, t in enumerate(ts):
                if q['close'][i] is None or q['open'][i] is None:
                    continue
                dt = datetime.datetime.fromtimestamp(t, HKT)
                rows.append((code, dt.strftime('%Y-%m-%d %H:%M'), q['open'][i],
                             q['high'][i], q['low'][i], q['close'][i],
                             q['volume'][i] or 0))
            return rows
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and a < tries - 1:
                time.sleep(5 * (a + 1) + random.random())
                continue
            return None
        except Exception:
            if a < tries - 1:
                time.sleep(2 + random.random())
                continue
            return None
    return None


ok, empty, failed, nbars = [], [], [], 0
with gzip.open(OUT + 'hk5m_small.csv.gz', 'wt', newline='') as fh:
    w = csv.writer(fh)
    w.writerow(['code', 'time_key', 'open', 'high', 'low', 'close', 'volume'])
    for i, code in enumerate(targets, 1):
        rows = fetch(code)
        if rows is None:
            failed.append(code)
        elif not rows:
            empty.append(code)
        else:
            w.writerows(rows)
            ok.append(code)
            nbars += len(rows)
        if i % 50 == 0:
            print('  %d/%d  ok=%d empty=%d fail=%d bars=%d' % (
                i, len(targets), len(ok), len(empty), len(failed), nbars), flush=True)
        time.sleep(0.25)

json.dump({'ok': ok, 'empty': empty, 'failed': failed, 'bars': nbars,
           'fetched_at': datetime.datetime.now(HKT).isoformat()},
          open(OUT + 'hk5m_small_manifest.json', 'w'))
print('DONE ok=%d empty=%d failed=%d bars=%d' % (len(ok), len(empty), len(failed), nbars), flush=True)
