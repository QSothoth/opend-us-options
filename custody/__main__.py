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
    sub.add_parser('fetch-train', add_help=False,
                   help='build the expanded paired raw/parent option cache (v2 window + OpenD options); not the formal train')
    sub.add_parser('fetch-train-0dte', add_help=False,
                   help='build the formal TRUE-0DTE paired custody train (expiry == trade_date only)')
    sub.add_parser('fetch-train-starter', add_help=False,
                   help='fetch the small real paired custody train starter from read-only OpenD (plumbing only)')
    sub.add_parser('eval-session', add_help=False,
                   help='replay a paired custody slice (validation/eval or train) with option-only PnL')
    sub.add_parser('baseline', add_help=False,
                   help='run must-trade custody v1 on a paired Release with next-bar option OHLCV fills')
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
    if args.command == 'fetch-train':
        from .train_window import main as train_window_main
        return train_window_main(rest)
    if args.command == 'fetch-train-0dte':
        from .train_0dte import main as train_0dte_main
        return train_0dte_main(rest)
    if args.command == 'fetch-train-starter':
        from .train_slice import main as train_main
        return train_main(rest)
    if args.command == 'eval-session':
        from .eval_session import main as session_main
        return session_main(rest)
    if args.command == 'baseline':
        from .baseline import main as baseline_main
        return baseline_main(rest)
    from .dryrun import main as dryrun_main
    return dryrun_main(rest)


if __name__ == '__main__':
    raise SystemExit(main())
