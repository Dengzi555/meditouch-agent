"""Topic-only questions must reach lookup without inventing symptoms or medicine suitability."""
import json
import unittest
from unittest.mock import patch
from sqlmodel import Session

from meditouch.inventory_models import DrugInventory, create_inventory_engine, create_inventory_tables
from meditouch.medical_candidates import query_candidates
from meditouch.pharmacy_graph import prepare_inventory_state, build_pharmacy_graph
from meditouch.pharmacy_semantics import understand_intake


class TopicModel:
    def __init__(self, topic):
        self.topic = topic

    def complete(self, messages):
        data = json.loads(messages[1]['content'])
        result = ({'script_id': next(iter(data['approved_scripts']))} if 'approved_scripts' in data else {
            'intent': 'find_drug', 'medical_topics': [self.topic], 'symptoms': [],
            'target_drugs': [], 'customer_tags': [], 'denied_symptoms': []})
        return {'role': 'assistant', 'content': json.dumps(result)}


class Rag:
    def __init__(self, names=()):
        self.requests = []
        self.names = names

    def invoke(self, args):
        self.requests.append(args)
        return {'recommended_drugs': list(self.names), 'source': 'llm_knowledge', 'evidence': []}


class PharmacyTopicTests(unittest.TestCase):
    def test_empty_medical_intent_does_not_search_whole_sentence_as_drug_name(self):
        class Empty:
            def complete(self, messages):
                return {'role': 'assistant', 'content': '{"intent":"find_drug","symptoms":[],"target_drugs":[],"customer_tags":[]}'}
        initial = prepare_inventory_state('帮我看看', self.workspace, model=Empty())
        state = build_pharmacy_graph(self.engine, model=Empty()).invoke(initial)
        self.assertEqual(state['intake_status'], 'needs_clarification')
        self.assertEqual(state['inventory_attempts'], 0)
        self.assertIn('尚未查询本店库存', state['answer'])

    def setUp(self):
        self.engine = create_inventory_engine('sqlite://')
        create_inventory_tables(self.engine)
        self.addCleanup(self.engine.dispose)
        self.workspace = {'store_id': 'A', 'task': 'drug_lookup', 'customer': {}}
        with Session(self.engine) as session:
            session.add(DrugInventory(name='复方感冒灵颗粒', brand='999', stock=10, shelf_location='A1'))
            session.add(DrugInventory(name='演示皮炎商品', stock=2, shelf_location='B1'))
            session.add(DrugInventory(name='演示其他候选', stock=2, shelf_location='B2'))
            session.commit()

    def test_topics_do_not_invent_symptoms_or_target_drugs(self):
        result, status, _ = understand_intake('感冒吃啥', {}, model=TopicModel('感冒'))
        self.assertEqual(result['medical_topics'], ['感冒'])
        self.assertEqual(result['symptoms'], [])
        self.assertEqual(result['target_drugs'], [])
        self.assertEqual(status, 'ok')

    def test_cold_and_unconfigured_new_topic_both_reach_catalog_and_rag(self):
        for topic, name in [('感冒', '复方感冒灵颗粒'), ('皮炎', '演示皮炎商品')]:
            with self.subTest(topic=topic):
                model, rag = TopicModel(topic), Rag()
                initial = prepare_inventory_state(topic + '吃啥', self.workspace, model=model)
                state = build_pharmacy_graph(self.engine, model=model, rag_tool=rag).invoke(initial)
                self.assertEqual(rag.requests[0]['medical_topics'], [topic])
                self.assertEqual(rag.requests[0]['symptoms'], [])
                self.assertIn(name, [item['name'] for item in state['inventory_results']])
                self.assertTrue(next(item for item in state['inventory_results'] if item['name'] == name)['topic_only'])
                self.assertEqual(state['safety_status'], 'unknown')
                self.assertTrue(any(item['node'] == 'safety_node' for item in state['trace']))
                self.assertTrue(any(item['id'] == 'topic-context' for item in state['ui_cards']))
                self.assertIn('主要有哪些不舒服', state['staff_script'])

    def test_topic_without_literal_product_still_uses_knowledge_then_checks_stock(self):
        model, rag = TopicModel('未知健康主题'), Rag(['演示其他候选'])
        initial = prepare_inventory_state('未知健康主题咨询', self.workspace, model=model)
        state = build_pharmacy_graph(self.engine, model=model, rag_tool=rag).invoke(initial)
        self.assertEqual(state['inventory_results'][0]['name'], '演示其他候选')
        self.assertFalse(state['inventory_results'][0].get('topic_only', False))
        self.assertEqual(state['safety_status'], 'unknown')

    def test_failed_knowledge_lookup_retains_catalog_facts(self):
        class Broken:
            def invoke(self, args):
                raise RuntimeError('test failure')
        model = TopicModel('感冒')
        state = build_pharmacy_graph(self.engine, model=model, rag_tool=Broken()).invoke(
            prepare_inventory_state('感冒吃啥', self.workspace, model=model))
        self.assertEqual(state['rag_status'], 'tool_error')
        self.assertEqual(state['inventory_results'][0]['name'], '复方感冒灵颗粒')
        self.assertTrue(state['inventory_results'][0]['topic_only'])

    def test_empty_knowledge_result_is_not_a_tool_failure(self):
        class Service:
            def search(self, *args, **kwargs):
                return {'items': []}
        class EmptyModel:
            def complete(self, messages):
                return {'role': 'assistant', 'content': json.dumps({
                    'recommended_drugs': [], 'source': 'retrieval', 'evidence_ids': []})}
        result = query_candidates([], [], [], medical_topics=['感冒'], model=EmptyModel(), service=Service())
        self.assertEqual(result['status'], 'not_found')
        self.assertEqual(result['source'], 'none')
