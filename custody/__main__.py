import argparse
import json
from .registry import Registry
from .replay import replay


def main(argv=None):
    parser = argparse.ArgumentParser(prog='custody')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('strategies')
    r = sub.add_parser('replay')
    r.add_argument('--strategy', required=True)
    r.add_argument('--symbol', required=True)
    r.add_argument('--direction', choices=['LONG', 'SHORT'], required=True)
    r.add_argument('--zip', required=True)
    r.add_argument('--out', required=True)
    sub.add_parser('dryrun', add_help=False,
                   help='poll read-only OpenD and advance a job without submitting orders')
    args, rest = parser.parse_known_args(argv)
    if args.command == 'strategies':
        print(json.dumps(Registry().list(), indent=2))
        return 0
    if args.command == 'replay':
        print(json.dumps(replay(args.strategy, args.symbol, args.direction, args.zip, args.out), indent=2))
        return 0
    from .dryrun import main as dryrun_main
    return dryrun_main(rest)


if __name__ == '__main__':
    raise SystemExit(main())
