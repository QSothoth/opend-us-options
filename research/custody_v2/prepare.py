"""Restore archived JSON and regenerate the training-only feature cache."""
import argparse
import gzip
from pathlib import Path
from data_boundary import training_slice
from search import load_data

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--train', required=True)
    args = ap.parse_args()
    training_slice(args.train)
    out = Path(__file__).parent / 'results'
    for source in sorted(out.glob('*.json.gz')):
        source.with_suffix('').write_bytes(gzip.decompress(source.read_bytes()))
    load_data(args.train, out)
    print('Archived JSON restored; cache rebuilt from verified training data only.')

if __name__ == '__main__':
    main()
