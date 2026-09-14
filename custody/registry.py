"""Versioned strategies: exact byte hashes and deep copies prevent silent mutation."""
from pathlib import Path
import hashlib, json


class Registry:
    def __init__(self, root=None):
        self.root = Path(root or Path(__file__).parent/'strategies')
        self.index = json.loads((self.root/'index.json').read_text())
        if self.index.get('schema_version') != 1: raise ValueError('unsupported registry schema')
        self._items = {}
        for name, meta in self.index['strategies'].items():
            path = (self.root/meta['file']).resolve()
            if not path.is_relative_to(self.root.resolve()): raise ValueError('invalid registry path')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != meta['sha256']: raise ValueError('strategy hash mismatch: '+name)
            doc = json.loads(raw)
            canonical = json.dumps({'entry': doc['case']['entry'], 'exit': doc['case']['exit']}, sort_keys=True).encode()
            if hashlib.sha256(canonical).hexdigest()[:12] != doc['case']['id']: raise ValueError('case ID mismatch')
            if doc['case']['id'] != meta['case_id']: raise ValueError('registry/case mismatch')
            self._items[name] = {**meta, 'strategy_id': name, 'config': doc}

    def get(self, name):
        if name not in self._items: raise ValueError('unknown strategy_id')
        return json.loads(json.dumps(self._items[name]))

    def list(self):
        return [{k:v for k,v in item.items() if k != 'config'} for item in self._items.values()]
