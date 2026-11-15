"""Published scores remain consistent with frozen datasets and per-case observations."""
import hashlib
import json
from pathlib import Path
import unittest

from scripts.evaluate_pharmacy import summarize

ROOT = Path(__file__).resolve().parent.parent


class PublishedEvaluationTests(unittest.TestCase):
    def read(self, name):
        return json.loads((ROOT / 'docs/evaluation' / name).read_text(encoding='utf-8'))

    def test_retrieval_counts_and_hashes_match_frozen_question_sets(self):
        for size, suite in [(100, 'real_retrieval_100.json'), (200, 'retrieval_suite_200.json')]:
            value = self.read(f'retrieval-{size}-summary.json')
            checksum = hashlib.sha256((ROOT / 'eval_data' / suite).read_bytes()).hexdigest()
            self.assertEqual(value['suite_sha256'], checksum)
            self.assertTrue(value['completed_utc'])
            self.assertEqual([r['arm'] for r in value['summary']], ['lexical', 'hybrid', 'rerank'])
            for row in value['summary']:
                self.assertEqual(row['questions'], size)
                self.assertEqual(row['timed_requests'], size * value['repeats'])
                self.assertTrue(0 <= row['hit_at_1'] <= row['hit_at_3'] <= row['hit_at_5'] <= size)
                self.assertTrue(0 <= row['mrr5'] <= 1)
                self.assertGreaterEqual(row['p95_ms'], row['p50_ms'])
            self.assertEqual(value['summary'][1]['hit_at_5'], value['summary'][2]['hit_at_5'])

    def test_workflow_summary_is_recomputed_from_thirty_observations(self):
        value = self.read('pharmacy-30-summary.json')
        self.assertTrue(value['complete'])
        self.assertTrue(value['code_unchanged_during_run'])
        self.assertEqual(len(value['cases']), 30)
        self.assertEqual(len({case['id'] for case in value['cases']}), 30)
        self.assertEqual(value['summary'], summarize(value['cases']))
        suite = ROOT / 'evals/pharmacy_copilot/golden_30.json'
        self.assertEqual(value['dataset_sha256'], hashlib.sha256(suite.read_bytes()).hexdigest())

    def test_index_points_to_matching_completed_reports(self):
        index = self.read('evaluation-summary.json')
        for key, filename, time_key in [
            ('retrieval_100', 'retrieval-100-summary.json', 'completed_utc'),
            ('retrieval_200', 'retrieval-200-summary.json', 'completed_utc'),
            ('pharmacy_30', 'pharmacy-30-summary.json', 'completed_at'),
        ]:
            report = self.read(filename)
            self.assertEqual(index[key]['summary'], report['summary'])
            self.assertEqual(index[key]['completed_utc'], report[time_key])
