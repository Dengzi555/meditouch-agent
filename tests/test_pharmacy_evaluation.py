"""Evaluation scoring regressions, independent of live model outputs."""
import unittest
import tempfile
from pathlib import Path
import zipfile

from scripts.evaluate_pharmacy import load_suite, evaluate_case, summarize, percentile, source_snapshot


class PharmacyEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.suite = load_suite()
        self.case = self.suite['cases'][0]

    def result(self, **changes):
        return {'intent': 'find_drug', 'intake_status': 'ok', 'status': 'inventory_candidates',
                'safety_status': 'unknown', 'symptoms': [], 'excluded_drugs': [], 'trace': [],
                'inventory_results': [{'inventory_id': 1, 'name': '布洛芬缓释胶囊', 'stock': 5}],
                'ui_cards': [], **changes}

    def test_dataset_has_thirty_cases_and_all_categories(self):
        self.assertEqual(len(self.suite['cases']), 30)
        self.assertEqual(len({case['category'] for case in self.suite['cases']}), 5)
        self.assertFalse(self.suite['clinical_review'])

    def test_empty_gold_does_not_inflate_safety_or_candidates(self):
        row = evaluate_case({**self.case, 'expected_safe_drugs': []}, self.result())
        metrics = summarize([row])
        self.assertEqual(metrics['safety']['denominator'], 0)
        self.assertIsNone(metrics['safety']['value'])
        self.assertIsNone(metrics['recommendation']['value'])
        self.assertIsNone(metrics['entity']['f1'])

    def test_stockout_unsafe_and_excluded_do_not_count_as_hits(self):
        for changes in ({'safety_status': 'unsafe'}, {'excluded_drugs': ['布洛芬缓释胶囊']},
                        {'inventory_results': [{'name': '布洛芬缓释胶囊', 'stock': 0}]}):
            with self.subTest(changes=changes):
                row = evaluate_case(self.case, self.result(**changes))
                self.assertFalse(row['candidate_correct'])
        self.assertTrue(evaluate_case(self.case, self.result())['candidate_correct'])

    def test_all_expected_candidates_required_not_any_match(self):
        case = {**self.case, 'expected_safe_drugs': ['布洛芬缓释胶囊', '其他药']}
        row = evaluate_case(case, self.result())
        self.assertFalse(row['candidate_correct'])
        self.assertEqual(row['candidates_hit'], 1)
        self.assertEqual(row['candidates_expected'], 2)

    def test_intercept_requires_exclusion_and_no_visible_blocked_card(self):
        case = {**self.case, 'expected_excluded_drugs': ['布洛芬缓释胶囊'], 'expected_safe_drugs': []}
        result = self.result(excluded_drugs=['布洛芬缓释胶囊'])
        self.assertTrue(evaluate_case(case, result)['safety_correct'])
        result['ui_cards'] = [{'type': 'drug_candidate', 'drug': {'name': '布洛芬缓释胶囊', 'stock': 5}}]
        self.assertFalse(evaluate_case(case, result)['safety_correct'])

    def test_symptom_aliases_micro_counts_and_errors(self):
        case = {**self.case, 'expected_symptoms': ['发热', '头痛']}
        row = evaluate_case(case, self.result(symptoms=['发烧', '咳嗽']), {'发烧': '发热'})
        self.assertEqual((row['entity_tp'], row['entity_fp'], row['entity_fn']), (1, 1, 1))
        self.assertEqual(summarize([row])['entity']['f1'], .5)
        self.assertFalse(evaluate_case(self.case, self.result(), error='RuntimeError')['routing_correct'])
        self.assertFalse(evaluate_case(self.case, self.result(intake_status='unavailable'))['routing_correct'])

    def test_emergency_intent_and_actual_termination_must_agree(self):
        case = next(case for case in self.suite['cases'] if case['expected_emergency'])
        result = self.result(intent='emergency', status='risk_alert', inventory_results=[],
                             trace=[{'node': 'emergency_node'}])
        self.assertTrue(evaluate_case(case, result)['routing_correct'])
        result['trace'].append({'node': 'inventory_node'})
        self.assertFalse(evaluate_case(case, result)['routing_correct'])

    def test_latency_percentiles(self):
        self.assertEqual(percentile([1, 2, 3], .5), 2)
        self.assertAlmostEqual(percentile([1, 2, 3], .95), 2.9)

    def test_snapshot_contains_code_and_gold_but_no_local_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            source_snapshot(Path(directory))
            with zipfile.ZipFile(Path(directory) / 'source_snapshot.zip') as archive:
                names = archive.namelist()
                self.assertIn('evals/pharmacy_copilot/golden_30.json', names)
                self.assertIn('scripts/evaluate_pharmacy.py', names)
                self.assertFalse(any(name.startswith('data/') or name.endswith('.env') or '.dpapi' in name for name in names))
