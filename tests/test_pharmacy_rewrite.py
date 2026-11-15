"""Standalone question propagation and history scope; synthetic model outputs, no paid calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from meditouch.pharmacy_graph import prepare_inventory_state, build_pharmacy_graph
from meditouch.pharmacy_semantics import understand_intake
from meditouch.medical_candidates import query_candidates
from meditouch.health_knowledge import lookup_health_evidence
from meditouch.chat_storage import ChatStore


class Model:
    def __init__(self, intake):
        self.intake, self.requests = intake, []

    def complete(self, messages):
        context = json.loads(messages[1]['content']); self.requests.append(context)
        result = ({'script_id': next(iter(context['approved_scripts']))}
                  if 'approved_scripts' in context else self.intake)
        return {'role': 'assistant', 'content': json.dumps(result, ensure_ascii=False)}


class RewriteTests(unittest.TestCase):
    def intake(self, **updates):
        value = {'intent': 'find_drug', 'medical_topics': ['感冒'], 'symptoms': [], 'target_drugs': [],
                 'standalone_query': '针对刚才咨询的感冒，本店有哪些待核查药品？',
                 'context_resolution': 'resolved', 'needs_inventory': True}
        value.update(updates)
        return value

    def test_topic_rewrite_keeps_original_and_does_not_invent_symptoms_or_drugs(self):
        history = [{'role': 'user', 'content': '感冒怎么办'},
                   {'role': 'assistant', 'content': '曾介绍一般知识，不代表已选药。'}]
        model = Model(self.intake())
        state = prepare_inventory_state('本店有什么推荐的药', {'store_id': 'A', 'task': 'drug_lookup', 'customer': {}}, model=model, history=history)
        self.assertEqual(state['user_query'], '本店有什么推荐的药')
        self.assertIn('感冒', state['standalone_query'])
        self.assertEqual(state['medical_topics'], ['感冒'])
        self.assertEqual(state['symptoms'], [])
        self.assertEqual(state['target_drugs'], [])
        self.assertEqual(model.requests[0]['history'], history)

    def test_ambiguous_reference_requests_clarification_without_any_lookup(self):
        question = '本店有什么推荐的药'
        model = Model(self.intake(medical_topics=[], context_resolution='ambiguous',
                                 clarification_question='你想继续咨询刚才哪一种情况？'))
        state = prepare_inventory_state(question, {'store_id': 'A', 'task': 'drug_lookup', 'customer': {}}, model=model)
        def forbidden(*args, **kwargs):
            self.fail('Ambiguous context must not trigger lookup')
        result = build_pharmacy_graph(None, model=model, tool_factory=forbidden).invoke(state)
        self.assertEqual(result['inventory_attempts'], 0)
        self.assertEqual(result['answer'], '你想继续咨询刚才哪一种情况？')

    def test_topic_switch_and_negative_facts_are_not_mechanically_merged(self):
        model = Model(self.intake(medical_topics=['腹泻'], standalone_query='为另一位顾客查询腹泻相关本店商品。',
                                 context_resolution='standalone', denied_symptoms=['发热']))
        result, status, _ = understand_intake('换个人，腹泻，没有发烧', {}, history=[{'role': 'user', 'content': '感冒了，孕妇'}], model=model)
        self.assertEqual(status, 'ok')
        self.assertEqual(result['medical_topics'], ['腹泻'])
        self.assertEqual(result['customer_tags'], [])
        self.assertEqual(result['denied_symptoms'], ['发热'])

    def test_candidate_rag_uses_complete_query_not_only_short_followup(self):
        calls = []
        class Service:
            def search(self, question, **kwargs):
                calls.append(question)
                return {'items': []}
        class Candidates:
            def complete(self, messages):
                return {'role': 'assistant', 'content': '{"recommended_drugs":[],"source":"none","evidence_ids":[]}'}
        query_candidates([], [], [], medical_topics=['感冒'], standalone_query='针对感冒查询本店待核查药品',
                         model=Candidates(), service=Service())
        self.assertIn('针对感冒查询本店待核查药品', calls[0])

    def test_local_health_retrieval_uses_standalone_query(self):
        calls = []
        class Service:
            def search(self, question, **kwargs):
                calls.append(question); return {'items': []}
        with patch.dict('os.environ', {'MED_AGENT_PUBLIC_KNOWLEDGE': '0'}):
            lookup_health_evidence({'user_query': '那流感呢', 'standalone_query': '流感有哪些常见症状？'}, local_service=Service())
        self.assertEqual(calls, ['流感有哪些常见症状？'])

    def test_completed_turn_persists_topic_and_rewrite_across_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'chat.db'
            store = ChatStore(path); chat = store.create('u', 's')['id']
            store.begin_turn(chat, 'u', 's', 'first', '感冒怎么办', {})
            store.finish_turn(chat, 'u', 's', 'first', {
                'medical_topics': ['感冒'], 'standalone_query': '感冒的一般处理原则是什么？',
                'context_resolution': 'standalone', 'ui_cards': []}, [])
            history = ChatStore(path).begin_turn(chat, 'u', 's', 'next', '本店有什么推荐的药', {})
            self.assertTrue(any('此前咨询主题（非确诊）：感冒' in item['content'] for item in history))
            self.assertTrue(any('感冒的一般处理原则是什么' in item['content'] for item in history))


if __name__ == '__main__':
    unittest.main()
