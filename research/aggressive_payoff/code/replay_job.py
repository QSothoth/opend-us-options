#!/usr/bin/env python3
"""Run the frozen candidate only for the user's chosen symbol and direction."""
import run as runtime
import argparse,json
from pathlib import Path
from data import load_release,make_cube,write_json,SHA256,TAG
from engine import JobSpec,aggregate_1m,daily_atr
from protocol import rule_for
from metrics import metric,DISCLAIMER
p=argparse.ArgumentParser();p.add_argument('--zip',required=True);p.add_argument('--rule',required=True);p.add_argument('--symbol',required=True);p.add_argument('--direction',choices=['LONG','SHORT'],required=True);p.add_argument('--flatten-et',default='15:45');p.add_argument('--out',required=True);a=p.parse_args()
rec=json.loads(Path(a.rule).read_text());assert rec['data_tag']==TAG and rec['sha256']==SHA256;c=rec['case'];rule=rule_for(c);job=JobSpec(a.symbol,a.direction,rule=rule,flatten_et=a.flatten_et)
frames,cal,audit,_=load_release(a.zip);raw=frames['klines_1m'];runtime.CUBES={s:make_cube(aggregate_1m(raw,s),cal,s) for s in {1,rule.signal_minutes}};runtime.EXE=runtime.CUBES[1];runtime.DAY=frames['klines_day'];n=c['entry']['daily_atr_days'];runtime.ADS={n:daily_atr(runtime.EXE,runtime.DAY,n)};runtime.BANKS={}
f,info=runtime.run_case(c,job=job,record_mtm=True);out=Path(a.out);out.mkdir(parents=True,exist_ok=True);f.to_csv(out/'job_trades.csv',index=False);write_json(out/'job_summary.json',runtime.clean({'disclaimer':DISCLAIMER,'symbol':job.symbol,'direction':job.direction,'max_one_round_trip_per_day':True,'metrics':metric(f),'rule':rec}))
print(f'{job.symbol} {job.direction}: {len(f)} sessions, {int(f.traded.sum())} trades; {DISCLAIMER}')
