import unittest

from custody.case_labels import SCHEMA, _move_tag, _range_bucket


class CaseLabelsUnit(unittest.TestCase):
    def test_schema(self):
        self.assertTrue(SCHEMA.startswith('custody-case-labels/'))

    def test_buckets(self):
        self.assertEqual(_range_bucket(1.0), 'quiet')
        self.assertEqual(_range_bucket(2.0), 'normal')
        self.assertEqual(_range_bucket(4.0), 'wide')
        self.assertEqual(_range_bucket(6.0), 'extreme')
        self.assertEqual(_move_tag(0.3), 'up')
        self.assertEqual(_move_tag(-0.3), 'down')
        self.assertEqual(_move_tag(0.0), 'flat')


if __name__ == '__main__':
    unittest.main()
