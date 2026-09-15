"""Rebuild only from the pinned training Release; never open acceptance data."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np

from custody.baseline import session_for
from custody.offline import OfflineMarket
from research.custody_v2.search import load_data
from research.custody_v2.data_boundary import load_training_cache,training_slice
from .engine import scheduled
from .metrics import label_path,LABELS


def prepare(root,work):
    work=Path(work);work.mkdir(parents=True,exist_ok=True)
    try:
        data=load_training_cache(root,work)
    except (FileNotFoundError,ValueError):
        data=load_data(root,work)
    features,prices,nb,dtes,blocks,meta=data
    manifest,cases,checked=training_slice(root)
    market=OfflineMarket(root,prefer_csv=True)
    labels=[]
    for c in cases:
        ss=session_for(c,manifest)
        bars=market.history_bars(c['symbol'],'K_1M',ss.opens,ss.closes)
        labels.append(label_path(bars,1 if c['direction']=='LONG' else -1))
    rng=np.random.default_rng(2026091503)
    entries=rng.integers(10,46,size=(len(cases),256))
    holds=rng.choice(np.array([5,15,30,60,120,240]),size=entries.shape)
    fixed_pairs=[(e,h) for e in (0,10,20,30,45) for h in (5,15,30,60,120,240,390)]
    fixed_e=np.tile(np.array([x[0] for x in fixed_pairs]),(len(cases),1))
    fixed_h=np.tile(np.array([x[1] for x in fixed_pairs]),(len(cases),1))
    arrays={'labels':np.array(labels),'dates':np.array([c['trade_date'] for c in cases]),'random_entries':entries,'random_holds':holds}
    for delay in (1,2,3):
        arrays['random'+str(delay)]=scheduled(prices,nb,entries,holds,delay)
        arrays['fixed'+str(delay)]=scheduled(prices,nb,fixed_e,fixed_h,delay)
    np.savez_compressed(work/'evaluation.npz',**arrays)
    summary={'scenarios':{name:{'cases':labels.count(k),'dates':len(set(c['trade_date'] for c,l in zip(cases,labels) if l==k))} for k,name in enumerate(LABELS)},
             'control_plans_sha256':hashlib.sha256(entries.tobytes()+holds.tobytes()).hexdigest(),
             'evaluation_sha256':hashlib.sha256((work/'evaluation.npz').read_bytes()).hexdigest(),
             'cache_sha256':hashlib.sha256((work/'cache.npz').read_bytes()).hexdigest(),
             'fixed_plans':fixed_pairs,'protocol_sha256':hashlib.sha256(Path(__file__).with_name('PROTOCOL.json').read_bytes()).hexdigest(),
             'random_complete_fraction':{str(d):float(np.mean(arrays['random'+str(d)][:,:,0]>=0)) for d in (1,2,3)}}
    (work/'EVALUATION_BINDING.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--train',required=True);ap.add_argument('--work',required=True);a=ap.parse_args();prepare(a.train,a.work)
