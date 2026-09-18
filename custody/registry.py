"""Registered strategy versions. Files are immutable: the index pins each file's SHA256.

A strategy file (``custody/strategies/<strategy_id>.json``) is immutable::

    {
      "schema_version": 2,
      "strategy_id": "zero_dte_timing_v6.1",
      "engine": "zero_dte_timing",            # key in custody.engines.ENGINES
      "description": "...",
      "developed_on": {"release": "...", "generation": "V5", "sessions_through": "YYYY-MM-DD"},
      "params": { ... validated by the engine ... }
    }

``index.json`` pins each file's SHA256 and carries the lifecycle status, which is the
only thing allowed to change: ``candidate`` -> ``accepted`` (after an ACCEPT verdict)
-> ``retired``. Never edit a registered file: new parameters = new strategy id.
"""
from pathlib import Path
import hashlib
import json

STATUSES = ('candidate', 'accepted', 'retired')


class Registry:
    def __init__(self, root=None):
        from .engines import ENGINES
        self.root = Path(root or Path(__file__).parent / 'strategies').resolve()
        index = json.loads((self.root / 'index.json').read_text(encoding='utf-8'))
        if index.get('schema_version') != 2:
            raise ValueError('unsupported strategy index schema')
        self._items = {}
        for strategy_id, meta in index['strategies'].items():
            path = (self.root / meta['file']).resolve()
            if not path.is_relative_to(self.root):
                raise ValueError('strategy file outside registry: ' + strategy_id)
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != meta['sha256']:
                raise ValueError('strategy file hash mismatch (registered files are immutable): ' + strategy_id)
            doc = json.loads(raw)
            if doc.get('schema_version') != 2 or doc.get('strategy_id') != strategy_id:
                raise ValueError('strategy file identity mismatch: ' + strategy_id)
            status = meta.get('status')
            if status not in STATUSES or 'status' in doc:
                raise ValueError('strategy status belongs in index.json only: ' + strategy_id)
            engine = ENGINES.get(doc.get('engine'))
            if engine is None:
                raise ValueError('unknown engine for %s: %r' % (strategy_id, doc.get('engine')))
            engine.validate(doc['params'])
            self._items[strategy_id] = {'strategy_id': strategy_id, 'sha256': digest, 'engine': doc['engine'],
                                        'status': status, 'description': doc.get('description', ''),
                                        'config': doc}
        self.default_id = index.get('default')
        if self.default_id not in self._items or self._items[self.default_id]['status'] == 'retired':
            raise ValueError('registry default must be a registered, non-retired strategy')

    def get(self, strategy_id):
        if strategy_id not in self._items:
            raise ValueError('unknown strategy_id: %r' % (strategy_id,))
        return json.loads(json.dumps(self._items[strategy_id]))

    def list(self):
        return [{k: v for k, v in item.items() if k != 'config'} for item in self._items.values()]
