import json
import unittest
from pathlib import Path

from custody import evaluate as ev
from custody.registry import Registry

ROOT = Path(__file__).resolve().parents[2]


class DocsTests(unittest.TestCase):
    def test_agents_and_claude_rules_are_identical(self):
        self.assertEqual((ROOT / 'AGENTS.md').read_text(), (ROOT / 'CLAUDE.md').read_text())

    def test_standard_document_matches_the_code_constants(self):
        text = (ROOT / 'docs' / 'STANDARD.md').read_text()
        for needle in ('%d%%' % round(100 * ev.PRIMARY_FILL.slippage_fraction), '$%.2f' % ev.PRIMARY_FILL.fee_per_contract,
                       '%g' % ev.MIN_PAYOFF_RATIO, str(ev.MIN_OOS_SESSIONS), str(ev.MIN_CASES_PER_SCENARIO),
                       str(ev.SPLIT_MINUTE), str(ev.LABEL_BAND), str(ev.NULL_DRAWS),
                       '%d%%' % round(100 * ev.NEIGHBOR_SCALE), 'G7', 'G8', 'G9', 'G10', 'settled_at_expiry'):
            self.assertIn(needle, text)

    def test_every_registered_strategy_has_a_committed_report(self):
        for item in (i for i in Registry().list() if i['status'] != 'retired'):
            reports = list((ROOT / 'reports' / item['strategy_id']).glob('*/report.json'))
            self.assertTrue(reports, item['strategy_id'])
            for path in reports:
                self.assertEqual(json.loads(path.read_text())['strategy']['sha256'], item['sha256'], path)


if __name__ == '__main__':
    unittest.main()
