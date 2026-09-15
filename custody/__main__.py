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
    sub.add_parser('fetch-eval', add_help=False,
                   help='fetch and freeze the real custody eval slice from read-only OpenD (once)')
    sub.add_parser('eval-session', add_help=False,
                   help='replay the frozen custody eval slice through the shared provider')
    args, rest = parser.parse_known_args(argv)
    if args.command == 'strategies':
        print(json.dumps(Registry().list(), indent=2))
        return 0
    if args.command == 'replay':
        print(json.dumps(replay(args.strategy, args.symbol, args.direction, args.zip, args.out), indent=2))
        return 0
    if args.command == 'fetch-eval':
        from .eval_slice import main as fetch_main
        return fetch_main(rest)
    if args.command == 'eval-session':
        from .eval_session import main as session_main
        return session_main(rest)
    from .dryrun import main as dryrun_main
    return dryrun_main(rest)


if __name__ == '__main__':
    raise SystemExit(main())
