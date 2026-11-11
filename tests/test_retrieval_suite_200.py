import collections
import json
import unittest
from pathlib import Path

from scripts.eval_retrieval_symptom_200 import load_chunks
from meditouch.rag_pipeline import search

ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / 'eval_data' / 'retrieval_suite_200.json'


class RetrievalSuiteTests(unittest.TestCase):
    def setUp(self):
        self.suite = json.loads(SUITE.read_text(encoding='utf-8'))
        self.questions = self.suite['questions']
        self.chunks = load_chunks()
        self.ids = {chunk['id'] for chunk in self.chunks}

    def test_shape_and_gold_labels(self):
        self.assertEqual(len(self.questions), 200)
        symptoms = collections.Counter(question['symptom'] for question in self.questions)
        self.assertEqual(len(symptoms), 10)
        self.assertTrue(all(count == 20 for count in symptoms.values()), symptoms)
        self.assertEqual(len({question['question'] for question in self.questions}), 200)
        self.assertEqual(len({question['id'] for question in self.questions}), 200)
        self.assertEqual(len(self.chunks), 120)
        variants = collections.Counter(question['variant'] for question in self.questions)
        self.assertEqual(variants, {'standard': 80, 'colloquial': 60, 'long_tail': 60})
        splits = collections.Counter(question['split'] for question in self.questions)
        self.assertEqual(splits, {'development': 160, 'regression': 40})
        for question in self.questions:
            self.assertTrue(question['gold'])
            self.assertTrue(set(question['gold']) <= self.ids, question['id'])

    def test_bm25_baseline_is_not_trivial_and_stays_above_floor(self):
        hits = []
        for question in self.questions:
            result = search(question['question'], self.chunks, 5)
            hits.append(any(item['id'] in question['gold'] for item in result))
        self.assertGreaterEqual(sum(hits) / len(hits), 0.65)

    def test_scope_is_explicitly_synthetic(self):
        self.assertIn('虚构', self.suite['scope'])
        self.assertIn('不代表医学准确率', self.suite['scope'])


if __name__ == '__main__':
    unittest.main()