"""Predeclared finite candidate library, independent of outcomes."""
import copy
import gzip
import hashlib
import json
import random
from pathlib import Path

from custody.adaptive import DEFAULT_ENTRY,DEFAULT_EXIT,ENTRY_KEYS,EXIT_KEYS
from custody.regime import make_strategy,DEFAULT_ROUTER

SEED=2026091503
FAMILIES=('balanced','trend_room','quick_failure','structure','recovery','range_reclaim')


def update(values, keys, **kw):
    out=list(values)
    for k,v in kw.items():out[keys.index(k)]=v
    return out


def identity(c):
    return hashlib.sha256(json.dumps({k:c[k] for k in ('profile','router','entries','exits')},sort_keys=True).encode()).hexdigest()[:16]


def strategy(c, name=None):
    return make_strategy(c['profile'],c['router'],c['entries'],c['exits'],name)


def generate(count=1536):
    rng=random.Random(SEED);out=[];seen=set();attempt=0
    while len(out)<count:
        family=FAMILIES[attempt%len(FAMILIES)];attempt+=1
        deadline=rng.choice((30,45,60));flatten=rng.choice((345,360,375))
        r=[rng.choice((.20,.30,.40)),rng.choice((15,20,25)),rng.choice((2,3,4)),rng.choice((2,3)),rng.choice((10,15,25)),rng.choice((.5,.75,1.)), -1.,float(family=='range_reclaim')]
        if family=='recovery':r[4]=30.
        e=update(DEFAULT_ENTRY,ENTRY_KEYS,warmup=10,deadline=deadline,relax=30,
                 early_score=4,late_score=3,rsi=rng.choice((0,5)),adx=r[1],
                 volume=rng.choice((.8,1.,1.2)),distance_cap=rng.choice((0.,2.,3.)),confirm=1)
        entries=[update(e,ENTRY_KEYS,mode=9,early_score=3,late_score=3),
                 update(e,ENTRY_KEYS,mode=rng.choice((2,5)),late_score=4),
                 update(e,ENTRY_KEYS,mode=rng.choice((3,8)),retest_depth=rng.choice((.5,1.))),
                 update(e,ENTRY_KEYS,mode=rng.choice((4,9)),early_score=3,late_score=3),
                 update(e,ENTRY_KEYS,mode=0)]
        clock=rng.choice((.5,1.))
        x=update(DEFAULT_EXIT,EXIT_KEYS,hard=4,soft=.75,grace=5,soft_escape=4,fail=15,
                 confirm=2,activation=4,trail=2,flatten=flatten,dte0_clock=clock,
                 dynamic_atr=1,against_mode=2,min_hold=5)
        trend_width=rng.choice((3.,4.,6.))
        exits=[update(x,EXIT_KEYS,soft=rng.choice((.5,.75,1.)),fail=rng.choice((5,10,20)),
                      activation=rng.choice((2,4)),trail=rng.choice((1.,2.)),stall=rng.choice((0,15,30))),
               update(x,EXIT_KEYS,soft=rng.choice((1.5,2.,3.)),grace=rng.choice((5,10)),
                      fail=rng.choice((30,60)),confirm=3,activation=rng.choice((4,6,8)),trail=trend_width,against_mode=0),
               update(x,EXIT_KEYS,soft=rng.choice((1.,1.5)),fail=rng.choice((20,30)),activation=rng.choice((4,6)),trail=rng.choice((2.,3.))),
               update(x,EXIT_KEYS,soft=rng.choice((.75,1.5)),fail=rng.choice((10,20)),activation=rng.choice((2,4)),trail=rng.choice((2.,3.))),
               update(x,EXIT_KEYS,soft=rng.choice((.5,1.)),fail=rng.choice((5,10)),fail_mode=1,
                      progress=.5,activation=0,trail=rng.choice((.75,1.)),soft_escape=0)]
        if family=='trend_room':
            exits[1]=update(exits[1],EXIT_KEYS,expand_at=12,expanded_trail=trend_width*1.5,activation=8)
            exits[2]=update(exits[2],EXIT_KEYS,trail=trend_width,activation=6)
        if family=='quick_failure':
            exits[0]=update(exits[0],EXIT_KEYS,fail=5,soft=.5)
            exits[4]=update(exits[4],EXIT_KEYS,fail=5,grace=3,soft=.5)
        if family=='structure':
            for j in (0,2,3):exits[j]=update(exits[j],EXIT_KEYS,structure=rng.choice((5,10)),structure_activation=2)
        c=dict(profile=rng.choice((1,4)),router=r,entries=entries,exits=exits,family=family)
        c['id']=identity(c)
        if c['id'] in seen:continue
        strategy(c);seen.add(c['id']);out.append(c)
    return out


def save(path, items):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(items,separators=(',',':')).encode(),mtime=0))


def neighbors(seeds, limit=384):
    out=[];seen={c['id'] for c in seeds}
    changes=[('router',j,s) for j in (0,1,2,3,4,5) for s in (-1,1)]
    changes += [('exit', (j,k),s) for j in range(5) for k in ('soft','fail','activation','trail') for s in (-1,1)]
    for change in changes:
        for parent in seeds:
            c=copy.deepcopy(parent);kind,key,sign=change
            if kind=='router':
                v=c['router'][key]
                c['router'][key]=v*(1+.2*sign) if key in (0,5) else max(1,int(round(v*(1+.2*sign))))
                if key==3:c['router'][key]=max(1,min(4,int(v)+sign))
            else:
                j,k=key;at=EXIT_KEYS.index(k);v=c['exits'][j][at]
                if v==0:continue
                c['exits'][j][at]=max(1,int(round(v*(1+.2*sign)))) if k=='fail' else v*(1+.2*sign)
            c['family']='local_neighbor';c['parent']=parent['id'];c['id']=identity(c)
            if c['id'] in seen:continue
            strategy(c);seen.add(c['id']);out.append(c)
            if len(out)>=limit:return out
    return out


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);a=ap.parse_args()
    items=generate();save(a.out,items)
    print(json.dumps({'candidates':len(items),'sha256':hashlib.sha256(Path(a.out).read_bytes()).hexdigest()}))
