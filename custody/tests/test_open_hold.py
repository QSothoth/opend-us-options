"""open_hold: the no-timing execution engine (AGENTS.md exception, 2026-09-20)."""
import sys
import unittest
from datetime import time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from helpers import session  # noqa: E402

from custody.engines.open_hold import OpenHold, validate_params  # noqa: E402
from custody.marketdata import Bar  # noqa: E402

PARAMS = {'entry_minute': 5, 'flatten_before_close_minutes': 15}


def run(params=PARAMS, direction='LONG', minutes=390):
    s = session()
    engine = OpenHold(params, direction, s, 100.0)
    out = []
    for i in range(1, minutes + 1):
        bar = Bar('US.TEST', s.opens + timedelta(minutes=i), 100.0, 100.0, 100.0, 100.0, 1000.0)
        d = engine.on_bar(bar)
        out.append((i, d.action, d.reason))
        if d.action == 'ENTER' and engine.phase == 'ENTERING':
            engine.on_entry_filled(bar.close_time, 100.0)
    return out


class OpenHoldTests(unittest.TestCase):
    def test_buys_at_the_configured_minute_and_holds_to_the_flatten(self):
        out = run()
        self.assertEqual([m for m, a, _ in out if a == 'ENTER'], [5])
        self.assertEqual([m for m, a, _ in out if a == 'WAIT'], [1, 2, 3, 4])
        exits = [m for m, a, _ in out if a == 'EXIT']
        self.assertEqual(exits[0], 375)                      # 390 - 15
        self.assertTrue(all(a == 'HOLD' for _, a, _ in out[5:374]))

    def test_entry_minute_moves_the_buy_and_nothing_else(self):
        out = run({**PARAMS, 'entry_minute': 30})
        self.assertEqual([m for m, a, _ in out if a == 'ENTER'], [30])
        self.assertEqual([m for m, a, _ in out if a == 'EXIT'][0], 375)

    def test_it_makes_no_decision_between_entry_and_flatten(self):
        reasons = {r for _, a, r in run() if a == 'EXIT'}
        self.assertEqual(reasons, {'scheduled_flatten'})     # no stop, no take-profit, ever

    def test_both_directions_behave_the_same(self):
        self.assertEqual([(m, a) for m, a, _ in run(direction='SHORT')],
                         [(m, a) for m, a, _ in run(direction='LONG')])

    def test_params_are_validated(self):
        self.assertEqual(validate_params(PARAMS), PARAMS)
        for bad in ({'entry_minute': 0}, {'entry_minute': 1.5}, {'entry_minute': True},
                    {'flatten_before_close_minutes': 10}):
            with self.assertRaises(ValueError, msg=str(bad)):
                validate_params({**PARAMS, **bad})
        with self.assertRaises(ValueError):
            validate_params({'entry_minute': 5})                      # missing flatten
        with self.assertRaises(ValueError):
            validate_params({**PARAMS, 'stop_max_atr': 2.0})          # unknown key

    def test_entry_must_precede_the_flatten_deadline(self):
        early = session(close=time(13))          # half day: 210 minutes, flatten at 90
        OpenHold({'entry_minute': 60, 'flatten_before_close_minutes': 120}, 'LONG', early, 100.0)
        with self.assertRaises(ValueError):      # entry would land at or after the mandatory exit
            OpenHold({'entry_minute': 120, 'flatten_before_close_minutes': 120}, 'LONG', early, 100.0)

    def test_strike_and_direction_are_checked(self):
        for bad in (0.0, -1.0, True):
            with self.assertRaises(ValueError, msg=str(bad)):
                OpenHold(PARAMS, 'LONG', session(), bad)
        with self.assertRaises(ValueError):
            OpenHold(PARAMS, 'FLAT', session(), 100.0)


if __name__ == '__main__':
    unittest.main()
