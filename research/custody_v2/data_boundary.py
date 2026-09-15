"""Pinned Release roles and content-bound training caches; no role overrides."""
import hashlib
import io
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
PINS = Path(__file__).with_name('DATA_PINS.json')
SCHEMA = 1

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def verify_release(root, role):
    """Check pinned metadata BEFORE reading any market-data file."""
    from custody.baseline import verify_slice
    if role not in ('train', 'validation'):
        raise ValueError('unknown dataset role')
    root = Path(root).resolve()
    pin = json.loads(PINS.read_text())[role]
    for name, expected in pin['metadata_sha256'].items():
        p = (root / name).resolve()
        if not p.is_relative_to(root) or digest(p.read_bytes()) != expected:
            raise ValueError('dataset identity/role mismatch: ' + role + '/' + name)
    manifest, cases, checked = verify_slice(root)
    if manifest.get('dataset') != pin['dataset'] or manifest.get('role') != pin['role'] or len(cases) != pin['count']:
        raise ValueError('dataset role or case count mismatch')
    dates = sorted({c['trade_date'] for c in cases})
    keys = sorted((c['contract'], c['trade_date']) for c in cases)
    if dates != pin['dates'] or digest(json.dumps(keys).encode()) != pin['case_keys_sha256']:
        raise ValueError('dataset case/date membership mismatch')
    other = json.loads(PINS.read_text())['validation' if role == 'train' else 'train']
    if set(dates) & set(other['dates']):
        raise ValueError('training/validation date overlap')
    return manifest, cases, checked

def training_slice(root):
    return verify_release(root, 'train')

def feature_source_hash():
    paths = ['custody/adaptive.py', 'custody/timing.py', 'custody/models.py',
             'custody/marketdata.py', 'custody/offline.py', 'custody/baseline.py',
             'research/custody_v2/search.py', 'research/custody_v2/data_boundary.py',
             'research/custody_v2/DATA_PINS.json']
    return digest(json.dumps({p: digest((REPO / p).read_bytes()) for p in paths}, sort_keys=True).encode())

def bind_cache(out):
    """Called only after load_data built a fresh cache from pinned training data."""
    out = Path(out)
    binding = {'schema': SCHEMA, 'role': 'train/custody', 'source_sha256': feature_source_hash(),
               'pins_sha256': digest(PINS.read_bytes()),
               'cache_sha256': digest((out/'cache.npz').read_bytes()),
               'metadata_sha256': digest((out/'cache_metadata.json').read_bytes())}
    (out/'CACHE_BINDING.json').write_text(json.dumps(binding, indent=2)+'\n')

def load_training_cache(root, out):
    """Every search/refinement/diagnostic entry must pass this gate first."""
    import numpy as np
    _, cases, _ = training_slice(root)
    out = Path(out)
    binding = json.loads((out/'CACHE_BINDING.json').read_text())
    raw = (out/'cache.npz').read_bytes()
    meta_raw = (out/'cache_metadata.json').read_bytes()
    expected = {'schema': SCHEMA, 'role': 'train/custody', 'source_sha256': feature_source_hash(),
                'pins_sha256': digest(PINS.read_bytes()), 'cache_sha256': digest(raw),
                'metadata_sha256': digest(meta_raw)}
    if binding != expected:
        raise ValueError('unbound, stale or modified training cache; rebuild using prepare.py')
    meta = json.loads(meta_raw)
    if meta.get('source') != 'custody-train-dte4' or meta['cases'] != cases:
        raise ValueError('cache case membership mismatch')
    # Parse exactly the bytes just hashed, not a second file open.
    with np.load(io.BytesIO(raw), allow_pickle=False) as z:
        arrays = [z[k].copy() for k in ('features', 'prices', 'next_bar', 'dtes', 'blocks')]
    if [a.shape for a in arrays] != [(6,124,391,31),(124,391),(124,391),(124,),(124,)]:
        raise ValueError('cache schema/shape mismatch')
    return (*arrays, meta)
