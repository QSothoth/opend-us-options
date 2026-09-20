"""custody command line.

    python3 -m custody strategies                                        # registered strategies
    python3 -m custody check    --dataset DIR                            # release requirements (both sides, pins)
    python3 -m custody evaluate --dataset DIR --out DIR [--strategy ID]  # offline evaluation on frozen history
    python3 -m custody label    --dataset DIR                            # ex-post case labels for stats
    python3 -m custody freeze   --dataset DIR [--date D] [--symbols ...] # daily read-only OpenD freeze
    python3 -m custody dryrun   --symbol S --direction D --contract C    # live quotes, simulated fills, no orders
    python3 -m custody run      --mode paper|live --acc-id N --symbol S --direction D --contract C  # OpenD orders
    python3 -m custody status   --db FILE [--job ID]                     # jobs and orders of a runtime database
    python3 -m custody stop     --db FILE --job ID                       # request exit; the running worker sells
"""
import json
import sys

COMMANDS = ('strategies', 'check', 'evaluate', 'label', 'freeze', 'dryrun', 'run', 'status', 'stop')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print(__doc__.strip())
        return 0 if argv[:1] in ([], ['-h'], ['--help']) else 2
    command, rest = argv[0], argv[1:]
    if command == 'strategies':
        from .registry import Registry
        registry = Registry()
        print(json.dumps({'default': registry.default_id, 'strategies': registry.list()}, indent=2, ensure_ascii=False))
        return 0
    if command == 'label':
        from .case_labels import main as entry
        return entry(rest)
    if command == 'check':
        from .dataset import main as entry
    elif command == 'evaluate':
        from .evaluate import main as entry
    elif command == 'freeze':
        from .freeze import main as entry
    else:
        from .runner import main as worker
        return worker(command, rest)
    return entry(rest)


if __name__ == '__main__':
    raise SystemExit(main())
