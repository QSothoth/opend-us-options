import json
import tempfile
import unittest
from pathlib import Path

from custody.case_labels import _move_tag, _range_bucket, _orb_break, SCHEMA
from custody.models import Bar
from datetime import datetime
from custody.models import ET


class CaseLabelsUnit(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(_range_bucket(1.0), 'quiet')
        self.assertEqual(_range_bucket(2.0), 'normal')
        self.assertEqual(_range_bucket(4.0), 'wide')
        self.assertEqual(_range_bucket(6.0), 'extreme')
        self.assertEqual(_move_tag(0.3), 'up')
        self.assertEqual(_move_tag(-0.3), 'down')
        self.assertEqual(_move_tag(0.0), 'flat')

    def test_orb(self):
        def bar(i, o, h, l, c):
            return Bar('US.X', datetime(2026, 9, 18, 9, 31 + i, tzinfo=ET), o, h, l, c, 1.0, '1m', 'frozen')
        bars = [bar(0, 100, 101, 99, 100.5)]
        for i in range(1, 20):
            bars.append(bar(i, 100.5, 101, 99.5, 100.2))
        bars.append(bar(20, 100.2, 102, 100, 101.5))  # close above orb high
        # rebuild simple: first 15 high=101 low=99, last close 101.5 -> break_up
        orb_bars = []
        for i in range(16):
            orb_bars.append(bar(i, 100, 101, 99, 100))
        orb_bars.append(bar(16, 100, 100.5, 99.5, 101.5))
        self.assertEqual(_orb_break(orb_bars, 15), 'break_up')


if __name__ == '__main__':
    unittest.main()
