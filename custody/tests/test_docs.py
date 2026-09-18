import json
import unittest
from pathlib import Path

from custody import evaluate as ev
from custody.registry import Registry

ROOT = Path(__file__).resolve().parents[2]


class DocsTests(unittest.TestCase):
    def test_claude_references_agents(self):
        self.assertTrue((ROOT / 'AGENTS.md').is_file())
        self.assertEqual((ROOT / 'CLAUDE.md').read_text(encoding='utf-8'), '@AGENTS.md\n')

    def test_standard_document_matches_the_code_constants(self):
        text = (ROOT / 'docs' / 'STANDARD.md').read_text(encoding='utf-8')
        for needle in ('%d%%' % round(100 * ev.PRIMARY_FILL.slippage_fraction), '$%.2f' % ev.PRIMARY_FILL.fee_per_contract,
                       '%g' % ev.MIN_PAYOFF_RATIO, str(ev.MIN_OOS_SESSIONS), str(ev.MIN_CASES_PER_SCENARIO),
                       str(ev.SPLIT_MINUTE), str(ev.LABEL_BAND), str(ev.NULL_DRAWS),
                       '%d%%' % round(100 * ev.NEIGHBOR_SCALE), 'G7', 'G8', 'G9', 'G10', 'settled_at_expiry'):
            self.assertIn(needle, text)
        for key, (name, scale, _) in ev.SCORE_TEXT.items():   # composite score: same items, scales and weights
            self.assertIn('| %s | %s | %d |' % (name, scale, ev.SCORE_WEIGHTS[key]), text)

    def test_both_sides_rule_is_written_everywhere_it_applies(self):
        for name in ('AGENTS.md', 'docs/DATA.md', 'docs/STANDARD.md'):
            text = (ROOT / name).read_text(encoding='utf-8')
            self.assertIn('CALL 和 PUT', text, name)
            self.assertIn('custody check', text, name)

    def test_every_registered_strategy_has_a_committed_report(self):
        for item in Registry().list():
            reports = list((ROOT / 'reports' / item['strategy_id']).glob('*/report.json'))
            self.assertTrue(reports, item['strategy_id'])
            for path in reports:
                report = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(report['strategy']['sha256'], item['sha256'], path)
                self.assertIn('completion_score', report['summary'], path)
                self.assertNotIn('G1_completion_100pct', report['gates_in_sample'], path)


if __name__ == '__main__':
    unittest.main()
