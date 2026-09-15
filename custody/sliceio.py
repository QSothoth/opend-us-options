"""Shared read/write helpers for frozen custody slices (validation/eval + train).

Both the validation/eval slice (:mod:`custody.eval_slice`) and the real paired
train starter (:mod:`custody.train_slice`) use the same on-disk layout:

.. code-block:: text

    <slice>/
      cases.json
      manifest.json
      underlying/<code>.csv|parquet
      option/<code>.csv|parquet

Keeping the writer in one place means train and validation cannot drift apart.
"""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from .offline import CASES_NAME, MANIFEST_NAME


def sha256(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def bar_frame(bars):
    import pandas as pd
    return pd.DataFrame([{
        'code': bar.code, 'close_time': bar.close_time.isoformat(), 'interval': bar.interval,
        'open': bar.open, 'high': bar.high, 'low': bar.low, 'close': bar.close, 'volume': bar.volume,
    } for bar in bars])


def write_series(root, kind, code, bars):
    """Write one CSV + parquet series and return its manifest entry."""
    folder = Path(root) / kind
    folder.mkdir(parents=True, exist_ok=True)
    csv_path = folder / (code + '.csv')
    parquet_path = folder / (code + '.parquet')
    frame = bar_frame(bars)
    frame.to_csv(csv_path, index=False)
    frame.to_parquet(parquet_path, index=False)
    return {
        'code': code, 'kind': kind, 'bar_count': len(bars),
        'first_bar': bars[0].close_time.isoformat() if bars else None,
        'last_bar': bars[-1].close_time.isoformat() if bars else None,
        'csv': str(csv_path.relative_to(root)), 'parquet': str(parquet_path.relative_to(root)),
        'sha256': {'csv': sha256(csv_path), 'parquet': sha256(parquet_path)},
    }


def write_slice_docs(root, cases_doc, manifest) -> None:
    root = Path(root)
    (root / CASES_NAME).write_text(_json(cases_doc))
    (root / MANIFEST_NAME).write_text(_json(manifest))


def zip_tree(root):
    root = Path(root)
    zip_path = root.with_suffix('.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(root.parent))
    return zip_path


def _json(doc):
    import json
    return json.dumps(doc, indent=2) + '\n'


__all__ = ['sha256', 'bar_frame', 'write_series', 'write_slice_docs', 'zip_tree']
