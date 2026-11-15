"""Admission regressions use synthetic text and metadata; no medical correctness claims."""
from copy import deepcopy
import json
import os
import unittest
from unittest.mock import patch

from meditouch.evidence_admission import admit_evidence, select_evidence
from meditouch.health_knowledge import lookup_health_evidence
from meditouch.medical_candidates import query_candidates
from meditouch.pharmacy_health import generate_health_answer


def source(**changes):
    return {'id': 'fixture', 'text': 'Synthetic supporting paragraph.',
            'source': {'title': 'Synthetic fixture', 'url': 'https://www.nhs.uk/conditions/common-cold/'},
            'review_status': 'pending', **changes}


class AdmissionTests(unittest.TestCase):
    def test_traceable_pending_reference_can_explain_but_is_not_approved(self):
        item = source()
        before = deepcopy(item)
        decision = admit_evidence(item, {'user_query': '感冒有什么表现'})
        self.assertTrue(decision['allowed'])
        self.assertEqual(decision['level'], 'general_reference')
        self.assertEqual(decision['review_status'], 'pending')
        self.assertFalse(decision['clinical_approval'])
        self.assertEqual(item, before)
        self.assertEqual(admit_evidence(item, {}, purpose='candidate_search')['code'], 'review_required')

    def test_explicit_generation_restrictions_cannot_be_overridden_by_approval(self):
        for restriction, code in (({'retrieval_only': True}, 'retrieval_only'),
                                  ({'generation_allowed': False}, 'generation_disabled')):
            with self.subTest(code=code):
                item = source(review_status='approved', **restriction)
                self.assertEqual(admit_evidence(item, {})['code'], code)

    def test_adult_evidence_is_rejected_for_child_and_pregnancy(self):
        for tags in (['儿童'], ['孕妇'], ['哺乳期'], ['成人', '儿童']):
            with self.subTest(tags=tags):
                decision = admit_evidence(source(audience='adult'), {'customer_tags': tags})
                self.assertEqual(decision['code'], 'population_out_of_scope')
        self.assertTrue(admit_evidence(source(audience='adult'), {'customer_tags': ['成人']})['allowed'])

    def test_age_limited_source_requires_explicit_customer_scope(self):
        self.assertEqual(admit_evidence(source(audience='adult'), {})['code'], 'population_not_established')
        state = {'workspace': {'customer': {'age': '8岁'}}}
        self.assertFalse(admit_evidence(source(audience='adult'), state)['allowed'])
        self.assertTrue(admit_evidence(source(audience='child'), state)['allowed'])

    def test_provider_scope_survives_cached_url_without_policy(self):
        item = source(kind='public', url='https://www.nhs.uk/symptoms/fever-in-adults/')
        self.assertFalse(admit_evidence(item, {'user_query': '孩子发烧怎么办'})['allowed'])
        self.assertTrue(admit_evidence(item, {'user_query': '成人发烧怎么办'})['allowed'])

    def test_negative_pregnancy_is_not_a_positive_tag(self):
        decision = admit_evidence(source(audience='adult'), {
            'user_query': '成人，没有怀孕，普通感冒怎么护理', 'customer_tags': ['成人']})
        self.assertTrue(decision['allowed'])

    def test_structured_conditions_and_topic_scope_are_required(self):
        item = source(applicability_policy={'question_terms': ['咳嗽'],
            'populations': ['成人'], 'conditions': {'duration': '超过一周'}})
        state = {'user_query': '咳嗽怎么护理', 'customer_tags': ['成人']}
        self.assertEqual(admit_evidence(item, state)['code'], 'condition_not_established')
        state['workspace'] = {'customer': {'duration': '超过一周'}}
        self.assertTrue(admit_evidence(item, state)['allowed'])
        self.assertEqual(admit_evidence(item, {**state, 'user_query': '腹痛怎么办'})['code'], 'question_out_of_scope')

    def test_bad_provenance_demo_revoked_and_changed_snapshot_are_rejected(self):
        cases = [(source(source={}), 'provenance_missing'),
                 (source(demo=True), 'demo_source'),
                 (source(review_status='revoked'), 'withdrawn_source'),
                 (source(snapshot_sha256='invalid'), 'snapshot_changed'),
                 (source(kind='public', url='https://untrusted.invalid/'), 'untrusted_public_source'),
                 (source(generation_allowed='false'), 'invalid_source')]
        for item, code in cases:
            with self.subTest(code=code):
                self.assertEqual(admit_evidence(item, {})['code'], code)

    def test_audit_is_text_free_and_does_not_mutate_record(self):
        item = source(retrieval_only=True)
        before = deepcopy(item)
        accepted, audit = select_evidence([None, item], {})
        self.assertEqual(accepted, [])
        self.assertNotIn(item['text'], json.dumps(audit))
        self.assertEqual(item, before)


class AdmissionIntegrationTests(unittest.TestCase):
    def test_filtered_public_result_falls_back_to_local_instead_of_stopping(self):
        class Local:
            def search(self, *args, **kwargs):
                return {'items': [source(id='local-ok')]}
        with patch.dict(os.environ, {'MED_AGENT_PUBLIC_KNOWLEDGE': '1',
                'MED_AGENT_NHS_API_KEY': '', 'MED_AGENT_SHOWAPI_APP_KEY': ''}), \
                patch('meditouch.health_knowledge.read_snapshot', return_value=None), \
                patch('meditouch.health_knowledge.write_snapshot'), \
                patch('meditouch.health_knowledge.medline_search', return_value=[source(
                    kind='public', url='https://www.nhs.uk/symptoms/fever-in-adults/')]):
            found, trace = lookup_health_evidence({'user_query': '孩子发烧', 'customer_tags': ['儿童'],
                'knowledge_queries': [{'en': 'Fever'}]}, local_service=Local())
        self.assertEqual([item['id'] for item in found], ['health-local-local-ok'])
        self.assertTrue(any(item.get('code') == 'population_out_of_scope' for item in trace))
        self.assertEqual(found[0]['review_status'], 'pending')

    def test_cached_blocked_source_is_checked_again(self):
        class Local:
            def search(self, *args, **kwargs):
                return {'items': []}
        with patch.dict(os.environ, {'MED_AGENT_PUBLIC_KNOWLEDGE': '1',
                'MED_AGENT_NHS_API_KEY': '', 'MED_AGENT_SHOWAPI_APP_KEY': ''}), \
                patch('meditouch.health_knowledge.read_snapshot', return_value=[source(retrieval_only=True)]), \
                patch('meditouch.health_knowledge.medline_search') as remote:
            found, trace = lookup_health_evidence({'knowledge_queries': [{'en': 'Fever'}]}, local_service=Local())
        self.assertEqual(found, [])
        remote.assert_not_called()
        self.assertTrue(any(item.get('code') == 'retrieval_only' for item in trace))

    def test_custom_health_loader_cannot_bypass_gate(self):
        requests = []
        class Model:
            def complete(self, messages):
                requests.append(json.loads(messages[1]['content']))
                return {'role': 'assistant', 'content': json.dumps({'sections': [{'title': '一般知识',
                    'items': [{'text': '先记录已经测量的体温。', 'highlights': [], 'citations': []}]}]})}
        state = {'user_query': '孩子发烧', 'customer_tags': ['儿童']}
        result = generate_health_answer(state, model=Model(), evidence_loader=lambda *a, **kw: (
            [source(audience='adult')], []))
        self.assertEqual(result['health_card']['basis'], 'model_knowledge')
        self.assertEqual(result['health_sources'], [])
        self.assertEqual(requests[0]['source_mode'], 'model_knowledge')
        self.assertEqual(requests[0]['sources'], [])
        self.assertTrue(any(item.get('code') == 'population_out_of_scope' for item in result['trace']))

    def test_candidate_terms_cannot_claim_retrieval_support_from_pending_reference(self):
        seen = []
        class Local:
            def search(self, *args, **kwargs):
                return {'items': [source(text='演示甲：测试段落')]}
        class Model:
            def complete(self, messages):
                seen.append(json.loads(messages[1]['content']))
                return {'role': 'assistant', 'content': json.dumps({
                    'recommended_drugs': ['演示甲'], 'source': 'llm_knowledge', 'evidence_ids': []})}
        with patch.dict(os.environ, {'MED_AGENT_ALLOW_KNOWLEDGE_CANDIDATES': '1'}):
            result = query_candidates(['测试症状'], [], [], model=Model(), service=Local())
        self.assertEqual(seen[0]['evidence'], [])
        self.assertEqual(result['source'], 'llm_knowledge')
        self.assertEqual(result['evidence'], [])
        self.assertEqual(result['evidence_admission'][0]['code'], 'review_required')
