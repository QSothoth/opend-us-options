"""Pinned real Release loader. No network, no OpenD, no synthetic evaluation."""
from __future__ import annotations
import hashlib, json, tempfile, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from pinned_parquet import read_parquet

SHA256 = 'df93506e498be259a9654c8bf82738aa8ed3604ec2936cf4dfbed0de26aba0c6'
TAG = 'eval-data-v2'
VERSION = 'opend_us_options_eval_v2'
SYMBOLS = sorted('US.'+s for s in 'SPY QQQ IWM AAPL NVDA TSLA MU AMD META MSFT'.split())
SCENARIOS = 'strong_up_trend strong_down_trend range_chop gap_up_open gap_down_open v_reversal_up v_reversal_down late_day_spike high_vol low_vol'.split()

def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')

def load_release(path):
    path=Path(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==SHA256, 'wrong Release SHA256'
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(path) as z:
        assert sum(i.file_size for i in z.infolist())<1_000_000_000
        for n in z.namelist():
            assert not n.startswith('/') and '..' not in Path(n).parts
        z.extractall(tmp)
        root=Path(tmp)/VERSION
        manifest=json.loads((root/'manifest.json').read_text())
        assert manifest['version']==VERSION
        for line in (root/'CHECKSUMS.sha256').read_text().splitlines():
            h,n=line.split();assert hashlib.sha256((root/n).read_bytes()).hexdigest()==h,n
        frames={n:read_parquet(root/(n+'.parquet')) for n in ['klines_day','klines_15m','klines_5m','klines_1m','scenario_labels']}
    for n,d in frames.items():
        assert len(d)==manifest['row_counts'][n]
        assert sorted(d.symbol.unique())==SYMBOLS
        if n=='scenario_labels':
            assert not d.duplicated(['symbol','date','grain']).any()
            continue
        assert not d.duplicated(['symbol','time']).any()
        assert np.isfinite(d[['open','high','low','close','volume']]).all().all()
        assert (d[['open','high','low','close']]>0).all().all() and (d.volume>=0).all()
        assert (d.low<=d[['open','close']].min(axis=1)).all()
        assert (d.high>=d[['open','close']].max(axis=1)).all()
        dt=pd.to_datetime(d.time,unit='s')
        d['date']=dt.dt.strftime('%Y-%m-%d')
        d['minute_close']=dt.dt.hour*60+dt.dt.minute
    cal=json.loads((Path(__file__).parent/'calendar_eval_v1.json').read_text())
    early=set(cal['early_close_dates'])
    for step in [1,5,15]:
        d=frames[f'klines_{step}m']
        dates=cal['dates'] if step!=1 else [x for x in cal['dates'] if x>='2026-05-14']
        assert sorted(d.date.unique())==dates
        assert len(d.groupby(['symbol','date']))==len(dates)*10
        for (sym,date),g in d.groupby(['symbol','date']):
            end=780 if date in early else 960
            assert g.sort_values('time').minute_close.tolist()==list(range(570+step,end+1,step)), (sym,date,step)
    audit={'tag':TAG,'sha256':SHA256,'manifest':manifest,'timestamp_convention':'ET wall clock encoded as UTC-like epoch; raw time is BAR CLOSE. Bar open time = raw time - interval. No UTC-to-ET conversion.',
           'calendar_source':cal['source'],'early_close_dates':sorted(early),'row_counts':{k:len(v) for k,v in frames.items()},'cross_resolution':{}}
    mismatch=[]
    for small,big,m in [(1,5,5),(5,15,15)]:
        a=frames[f'klines_{small}m'].copy();a['bucket']=((a.time-1)//(m*60)+1)*(m*60)
        a=a.groupby(['symbol','bucket']).agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum'))
        b=frames[f'klines_{big}m'].set_index(['symbol','time']);b.index.names=a.index.names
        j=a.join(b,rsuffix='_source',how='inner')
        stats={}
        for c in ['open','high','low','close','volume']:
            dif=(j[c]-j[c+'_source']).abs();stats[c]={'different_gt_1e6':int((dif>1e-6).sum()),'max_abs':float(dif.max())}
        audit['cross_resolution'][f'{small}m_to_{big}m']={'rows':len(j),**stats}
        bad=j[(j[['open','close']].to_numpy()-j[['open_source','close_source']].to_numpy()).__abs__().max(axis=1)>1e-6].reset_index()
        for _,r in bad.iterrows():mismatch.append({'small':small,'large':big,'symbol':r.symbol,'date':str(pd.to_datetime(r.bucket,unit='s').date()),'bar_close_et':str(pd.to_datetime(r.bucket,unit='s')),'small_open':r.open,'large_open':r.open_source,'small_close':r.close,'large_close':r.close_source})
    return frames,cal,audit,pd.DataFrame(mismatch)

def roll(a,n,method='mean',shift=0):
    f=pd.DataFrame(a.T)
    if shift:f=f.shift(shift)
    return getattr(f.rolling(n,min_periods=n),method)().to_numpy().T

def make_cube(frame, cal, step):
    keys=frame[['date','symbol']].drop_duplicates().sort_values(['date','symbol']).reset_index(drop=True)
    arrays={c:np.full((len(keys),390//step),np.nan) for c in ['open','high','low','close','volume']}
    groups={(date,sym):g.sort_values('time') for (date,sym),g in frame.groupby(['date','symbol'])}
    for i,(date,sym) in enumerate(keys.itertuples(index=False,name=None)):
        g=groups[(date,sym)]
        for c in arrays:arrays[c][i,:len(g)]=g[c]
    keys['flatten']=np.where(keys.date.isin(cal['early_close_dates']),765,945)
    keys['session_close']=keys.flatten+15
    return {'keys':keys,'step':step,**arrays}

def features(cube,day):
    """All rolling calculations run along time or PRIOR sessions only."""
    f={};c,h,l,v=(cube[x] for x in ['close','high','low','volume']);keys=cube['keys']
    prev=np.concatenate([cube['open'][:,:1],c[:,:-1]],axis=1)
    tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
    for mins in [35,70]:f[f'atr{mins}']=roll(tr,mins//5)
    changes=np.abs(c-prev)
    for mins in [30,60,90]:
        n=mins//5;lag=np.concatenate([np.repeat(np.nan,c.shape[0]*n).reshape(c.shape[0],n),c[:,:-n]],axis=1)
        f[f'er{mins}']=abs(c-lag)/np.maximum(roll(changes,n,'sum'),1e-12)
    for mins in [30,45,60,75,90]:
        n=mins//5
        # Value is unavailable before the opening window has completed.
        f[f'orhigh{mins}']=np.maximum.accumulate(np.where(np.isnan(h),-np.inf,h),axis=1)
        f[f'orlow{mins}']=np.minimum.accumulate(np.where(np.isnan(l),np.inf,l),axis=1)
        for name in [f'orhigh{mins}',f'orlow{mins}']:
            a=f[name];a[:,n:]=a[:,n-1:n];a[:,:n-1]=np.nan
    f['vwap']=np.cumsum(np.nan_to_num((h+l+c)/3*v),axis=1)/np.maximum(np.cumsum(np.nan_to_num(v),axis=1),1)
    f['vwap_prev']=np.concatenate([np.full((len(c),1),np.nan),f['vwap'][:,:-1]],axis=1)
    f['prevclose']=prev
    for mins in [10,15,20]:
        f[f'low{mins}']=roll(l,mins//5,'min',1)
        f[f'high{mins}']=roll(h,mins//5,'max',1)
    for mins in [10,20,40]:
        n=mins//5;lag=np.concatenate([np.full((len(c),n),np.nan),c[:,:-n]],axis=1)
        ret=c/lag-1
        ref=ret[keys.index[keys.symbol=='US.SPY'].to_numpy()]
        qref=ret[keys.index[keys.symbol=='US.QQQ'].to_numpy()]
        # Same timestamp benchmark, human symbol retained; SPY uses QQQ reference.
        f[f'rs{mins}']=ret-np.repeat(ref,10,axis=0)
        ix=keys.symbol.to_numpy()=='US.SPY';f[f'rs{mins}'][ix]=ret[ix]-qref
    for look in [10,20]:
        for mode,a in [('bar',v),('cumulative',np.cumsum(np.nan_to_num(v),axis=1))]:
            rv=np.full_like(c,np.nan)
            for sym in SYMBOLS:
                ix=keys.index[keys.symbol==sym].to_numpy()
                avg=pd.DataFrame(a[ix]).shift(1).rolling(look,min_periods=look).mean().to_numpy()
                rv[ix]=a[ix]/np.maximum(avg,1)
            f[f'rvol_{mode}_{look}']=rv
    # Completed 30-minute bandwidth versus PRIOR 20 sessions at same clock.
    width=4*roll(c,6,'std')/roll(c,6)
    for q in [.2,.4]:
        thresh=np.full_like(c,np.nan)
        for sym in SYMBOLS:
            ix=keys.index[keys.symbol==sym].to_numpy()
            thresh[ix]=pd.DataFrame(width[ix]).shift(1).rolling(20,min_periods=20).quantile(q).to_numpy()
        prior_w=np.concatenate([np.full((len(c),1),np.nan),width[:,:-1]],axis=1)
        prior_t=np.concatenate([np.full((len(c),1),np.nan),thresh[:,:-1]],axis=1)
        compressed=prior_w<=prior_t
        f[f'compressed_{q}']=roll(compressed.astype(float),6,'max')>0
    f['range_expansion']=(h-l)/np.maximum(roll(h-l,6,'mean',1),1e-12)
    daily=[]
    for sym,g in day.groupby('symbol'):
        g=g.sort_values('time').copy();pc=g.close.shift(1)
        g['atr_day']=pd.concat([g.high-g.low,abs(g.high-pc),abs(g.low-pc)],axis=1).max(axis=1).rolling(20,min_periods=20).mean()
        for n in [20,50]:
            g[f'ema{n}']=g.close.ewm(span=n,adjust=False,min_periods=n).mean()
            for s in [1,3]:g[f'slope{n}_{s}']=g[f'ema{n}'].diff(s)
        cols=['atr_day','high','low','close']+[f'ema{n}' for n in [20,50]]+[f'slope{n}_{s}' for n in [20,50] for s in [1,3]]
        # Shift daily values BEFORE joining the session date: current daily OHLC never exposed.
        past=g[cols].shift(1);past['date']=g.date;past['symbol']=sym
        daily.append(past)
    prior=keys[['date','symbol']].merge(pd.concat(daily),on=['date','symbol'],validate='one_to_one')
    assert prior.drop(columns=['date','symbol']).notna().all().all()
    for col in prior.columns[2:]:f['daily_'+col]=prior[col].to_numpy()
    return f
