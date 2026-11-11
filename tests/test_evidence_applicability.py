import unittest

from meditouch.agent_graph import run_graph
from meditouch.evidence_service import EvidenceService, adjudicate, fingerprint, validate_answer
from scripts.agent_acceptance import fixture_service
from scripts.patient_acceptance import AcceptanceModel


class ApplicabilityTests(unittest.TestCase):
    def test_forged_or_missing_condition_cannot_authorize_real_citation(self):
        item = fixture_service().all_items('demo')[0]
        item['demo'] = False
        item['applicability_policy'] = {
            'question_terms': ['适用范围'],
            'populations': ['成人'],
            'conditions': {'duration': '不到一天'},
        }
        item['snapshot_sha256'] = fingerprint(item)
        question = '演示措施X的适用范围'
        draft = {'status': 'answer', 'citations': [{'evidence_id': item['id']}]}
        result = validate_answer(draft, [item], adjudicate([item]), question=question,
                                 context={'population': '成人'})
        self.assertEqual(result['applicability'][item['id']], 'condition_not_established')
        self.assertFalse(result['citations'])
        draft['citations'][0]['applicability_policy'] = {'conditions': {}}
        result = validate_answer(draft, [item], adjudicate([item]), question=question,
                                 context={'population': '成人', 'duration': '不到一天'})
        self.assertIn('CITATION_SCHEMA', result['errors'])

    def test_graph_rejects_wrong_question_and_population(self):
        items = fixture_service().all_items('demo')
        item = items[0]
        item['applicability_policy'] = {
            'question_terms': ['适用范围'], 'populations': ['成人'],
        }
        item['snapshot_sha256'] = fingerprint(item)
        service = EvidenceService(items=items)
        for question, population, expected, reason in [
            ('演示措施X的适用范围', '成人', 'answered', 'eligible'),
            ('演示措施X的适用范围', '儿童', 'insufficient', 'population_not_established'),
            ('演示措施X的适用范围', '不清楚', 'insufficient', 'population_not_established'),
            ('演示措施X的价格', '成人', 'insufficient', 'question_out_of_scope'),
        ]:
            with self.subTest(question=question, population=population):
                result = run_graph(question, corpus='demo', known_context={'population': population},
                                   service=service, model=AcceptanceModel())
                self.assertEqual(result['status'], expected)
                trace = next(t for t in result['trace'] if t['node'] == 'evidence_applicability')
                self.assertIn(reason, trace['result'].values())
                if expected == 'insufficient':
                    self.assertFalse(result['citations'])
