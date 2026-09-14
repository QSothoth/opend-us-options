import argparse,json
from .registry import Registry
from .replay import replay
p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True);sub.add_parser('strategies')
r=sub.add_parser('replay');r.add_argument('--strategy',required=True);r.add_argument('--symbol',required=True);r.add_argument('--direction',choices=['LONG','SHORT'],required=True);r.add_argument('--zip',required=True);r.add_argument('--out',required=True)
a=p.parse_args()
print(json.dumps(Registry().list() if a.command=='strategies' else replay(a.strategy,a.symbol,a.direction,a.zip,a.out),indent=2))
