import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from scripts.flu_pilot_acceptance import main


class FluPilotAcceptanceTests(unittest.TestCase):
    def test_review_gate_and_report_validation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'eval_data').mkdir()
            (root / 'data').mkdir()
            (root / 'eval_data/flu_pilot_holdout.json').write_text(json.dumps({
                'cases': [{'id': 'test', 'question': 'Synthetic question',
                           'expected_status': 'answered', 'required_statement_ids': ['s1']}],
            }), encoding='utf-8')
            service = Mock()
            service.all_items.return_value = []
            runner = Mock()
            self.assertEqual(main(service, runner, root), 2)
            runner.assert_not_called()
            service.all_items.return_value = [{
                'id': 'e1', 'raw_quote': 'Synthetic source',
                'reviewed_statements': [{'id': 's1', 'limitations': 'Test only', 'applicability': 'Fixture'}],
            }]
            result = {
                'status': 'answered', 'answer': 'Fixture. Test only.',
                'statements': [{'statement_id': 's1'}],
                'citations': [{'statement_id': 's1', 'evidence_id': 'e1', 'quote': 'Synthetic source'}],
            }
            runner.return_value = result
            self.assertEqual(main(service, runner, root), 0)
            for change in [{'answer': 'Fixture.'}, {'citations': []}]:
                runner.return_value = {**result, **change}
                self.assertEqual(main(service, runner, root), 1)
                report = json.loads((root / 'data/flu-pilot-acceptance.json').read_text(encoding='utf-8'))
                self.assertFalse(report['cases'][0]['passed'])
