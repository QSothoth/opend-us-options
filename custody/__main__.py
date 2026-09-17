"""custody command line.

    python3 -m custody strategies                                     # registered strategies
    python3 -m custody check    --dataset DIR                         # release requirements (both sides, pins)
    python3 -m custody evaluate --dataset DIR --out DIR [--strategy ID] # the standard (offline)
    python3 -m custody freeze   --dataset DIR [--date D] [--symbols ...] # daily read-only OpenD freeze
    python3 -m custody dryrun   --symbol S --direction D --contract C   # live read-only, never orders
"""
import json
import sys

COMMANDS = ('strategies', 'check', 'evaluate', 'freeze', 'dryrun')


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
    if command == 'check':
        from .dataset import main as run
    elif command == 'evaluate':
        from .evaluate import main as run
    elif command == 'freeze':
        from .freeze import main as run
    else:
        from .dryrun import main as run
    return run(rest)


if __name__ == '__main__':
    raise SystemExit(main())
