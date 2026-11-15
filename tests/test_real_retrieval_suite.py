import collections
import json
import unittest

from scripts.eval_retrieval_ab import ROOT, SUITE, load_suite, rank, summarize


class RealRetrievalSuiteTests(unittest.TestCase):
    def test_100_distinct_sections_and_balanced_question_styles(self):
        suite = json.loads(SUITE.read_text(encoding='utf-8'))
        cases = suite['questions']
        self.assertEqual(len(cases), 100)
        self.assertEqual(len({c['gold'][0] for c in cases}), 100)
        self.assertEqual(len({c['topic'] for c in cases}), 20)
        self.assertEqual(collections.Counter(c['kind'] for c in cases), {'direct': 50, 'colloquial': 50})
        self.assertIn('不是全新盲测', suite['provenance'])
        self.assertTrue(all(c['source_url'].startswith('https://www.nhs.uk/') for c in cases))

    @unittest.skipUnless((ROOT / 'rag_data/symptom_references/chunks.json').exists(), 'Local public corpus required')
    def test_gold_points_to_frozen_real_source_text(self):
        items = json.loads((ROOT / 'rag_data/symptom_references/chunks.json').read_text(encoding='utf-8'))
        self.assertEqual(len(load_suite(items)['questions']), 100)
        altered = [dict(i, raw_quote='changed') for i in items]
        with self.assertRaisesRegex(ValueError, 'Gold source changed'):
            load_suite(altered)

    def test_repeated_timings_do_not_inflate_accuracy_denominator(self):
        rows = [dict(id=case, repeat=repeat, lexical=value, hybrid=value, rerank=value,
                     lexical_ms=1, hybrid_ms=2, rerank_ms=3)
                for repeat in (1, 2, 3) for case, value in (('a', 2), ('b', None))]
        for result in summarize(rows):
            self.assertEqual(result['questions'], 2)
            self.assertEqual(result['timed_requests'], 6)
            self.assertEqual(result['hit_at_1'], 0)
            self.assertEqual(result['hit_at_5'], 1)
            self.assertEqual(result['mrr5'], .25)

    def test_exact_section_labels_do_not_accept_same_source_siblings(self):
        self.assertIsNone(rank([{'id': 'source-2'}], ['source-1']))
        self.assertEqual(rank([{'id': 'other'}, {'id': 'source-1'}], ['source-1']), 2)


if __name__ == '__main__':
    unittest.main()
