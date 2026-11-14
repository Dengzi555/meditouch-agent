"""Five workflows, memory and knowledge provenance on synthetic stock; no paid API calls."""
import json
import unittest
from unittest.mock import patch

from sqlmodel import Session

from meditouch.inventory_models import DrugInventory, DrugDetails, create_inventory_engine, create_inventory_tables
from meditouch.inventory_tools import search_inventory_batch
from meditouch.medical_candidates import query_candidates
from meditouch.pharmacy_graph import build_pharmacy_graph, prepare_inventory_state, route_start
from meditouch.pharmacy_semantics import understand_intake
from meditouch.pharmacy_ui import graph_ui_events


class Model:
    def __init__(self, intake):
        self.intake = intake
        self.requests = []

    def complete(self, messages):
        data = json.loads(messages[1]['content'])
        self.requests.append(data)
        result = ({'script_id': next(iter(data['approved_scripts']))}
                  if 'approved_scripts' in data else self.intake)
        return {'role': 'assistant', 'content': json.dumps(result)}


class Rag:
    def __init__(self, names):
        self.names, self.requests = names, []

    def invoke(self, args):
        self.requests.append(args)
        return {'recommended_drugs': [name for name in self.names if name not in args['excluded_drugs']],
                'source': 'llm_knowledge', 'evidence': []}


class SemanticWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_inventory_engine('sqlite://')
        create_inventory_tables(self.engine)
        self.addCleanup(self.engine.dispose)
        self.workspace = {'store_id': 'A', 'task': 'drug_lookup', 'customer': {}}

    def seed(self, name, *, stock=5, contraindications=None, ingredients=None):
        with Session(self.engine) as session:
            item = DrugInventory(name=name, stock=stock, shelf_location='A1')
            session.add(item); session.flush()
            session.add(DrugDetails(inventory_id=item.id, contraindications=contraindications, ingredients=ingredients))
            session.commit()

    def state(self, *, names=None, symptoms=None, tags=None, intent='find_drug', history=None):
        model = Model({'intent': intent, 'symptoms': symptoms or [], 'target_drugs': names or [],
                       'customer_tags': tags or []})
        state = prepare_inventory_state('店员口语咨询：' + '、'.join(names or []), self.workspace, model=model, history=history)
        return state, model

    def test_basic_batch_fuzzy_lookup_deduplicates_skus(self):
        self.seed('演示甲缓释胶囊')
        self.seed('演示乙颗粒')
        state, model = self.state(names=['演示甲', '演示乙'])
        result = build_pharmacy_graph(self.engine, model=model).invoke(state)
        self.assertEqual(len(result['inventory_results']), 2)
        self.assertEqual(result['inventory_results'][0]['name_match'], 'fuzzy')
        self.assertEqual(result['combination_status'], 'unknown')
        self.assertEqual(len(search_inventory_batch(self.engine, ['演示甲', '演示甲缓释胶囊'])['items']), 1)

    def test_unsafe_retrieves_again_and_never_reintroduces_excluded(self):
        self.seed('演示甲', contraindications='孕妇禁用')
        self.seed('演示乙')
        state, model = self.state(names=['演示甲'], symptoms=['咽痛'], tags=['孕妇'])
        rag = Rag(['演示甲', '演示乙'])
        result = build_pharmacy_graph(self.engine, model=model, rag_tool=rag).invoke(state)
        self.assertEqual(result['excluded_drugs'], ['演示甲'])
        self.assertEqual(rag.requests[0]['excluded_drugs'], ['演示甲'])
        self.assertEqual([item['name'] for item in result['inventory_results']], ['演示乙'])
        self.assertEqual(result['safety_status'], 'unknown')
        self.assertEqual(result['safety_checks'][0]['name'], '演示乙')

    def test_combination_cannot_be_approved_and_duplicate_ingredient_is_reported(self):
        self.seed('演示甲', ingredients='演示成分')
        self.seed('演示乙', ingredients='演示成分')
        state, model = self.state(names=['演示甲', '演示乙'], intent='check_safety')
        result = build_pharmacy_graph(self.engine, model=model).invoke(state)
        self.assertEqual(result['safety_status'], 'unknown')
        self.assertIn('重复成分', result['safety_reason'])
        self.assertTrue(any(card['id'] == 'combination' for card in result['ui_cards']))

    def test_stockout_searches_alternative_and_preserves_stockout_record(self):
        self.seed('演示甲', stock=0)
        self.seed('演示乙')
        state, model = self.state(names=['演示甲'], symptoms=['腹泻'])
        rag = Rag(['演示甲', '演示乙'])
        result = build_pharmacy_graph(self.engine, model=model, rag_tool=rag).invoke(state)
        self.assertIn('演示甲', rag.requests[0]['excluded_drugs'])
        self.assertEqual(result['stockout_results'][0]['stock'], 0)
        self.assertEqual([item['name'] for item in result['inventory_results']], ['演示乙'])
        self.assertEqual(result['safety_status'], 'unknown')

    def test_semantic_emergency_is_terminal_and_calls_no_inventory_or_rag(self):
        state, model = self.state(intent='emergency', symptoms=['胸痛'])
        with patch('meditouch.pharmacy_graph.build_inventory_tools', side_effect=AssertionError('No SQL')), \
                patch('meditouch.pharmacy_graph.search_inventory_batch', side_effect=AssertionError('No SQL')):
            events = list(graph_ui_events(build_pharmacy_graph(self.engine, model=model), state, 'emergency'))
        self.assertEqual(events[-1]['event'], 'done')
        self.assertEqual(events[-1]['payload']['inventory_attempts'], 0)
        self.assertEqual(route_start(state), 'emergency_node')
        self.assertFalse(any(event['payload'].get('stage') == 'inventory' for event in events))

    def test_host_emergency_survives_failed_model(self):
        model = Model({'intent': 'chitchat'})
        result, status, calls = understand_intake('顾客现在喘不上气', {}, model=model)
        self.assertEqual(result['intent'], 'emergency')
        self.assertEqual(calls, 0)
        self.assertEqual(model.requests, [])

    def test_history_passed_with_denials_and_new_symptoms(self):
        history = [{'role': 'user', 'content': '刚才头疼'}]
        model = Model({'intent': 'find_drug', 'symptoms': ['头痛', '发热'], 'target_drugs': [],
                       'customer_tags': [], 'denied_symptoms': ['流鼻涕']})
        intake, status, _ = understand_intake('现在也烧起来了，没有鼻子流水', {}, history=history, model=model)
        self.assertEqual(model.requests[0]['history'], history)
        self.assertEqual(intake['symptoms'], ['头痛', '发热'])
        self.assertEqual(intake['denied_symptoms'], ['流鼻涕'])
        self.assertEqual(status, 'ok')

    def test_chitchat_never_queries_inventory_and_invalid_intake_fails_closed(self):
        state, model = self.state(intent='chitchat')
        model.intake = {}  # General generator may fall back, but no medical tools may run.
        result = build_pharmacy_graph(self.engine, model=model).invoke(state)
        self.assertEqual(result['outcome'], 'general_text')
        self.assertEqual(result['tool_calls'], 0)
        state = prepare_inventory_state('口语问题', self.workspace, model=model)
        self.assertEqual(state['intake_status'], 'unavailable')
        self.assertEqual(route_start(state), 'fallback_node')

    def test_retry_budget_and_empty_rag_stop(self):
        state, model = self.state(symptoms=['未知症状'])
        rag = Rag([])
        result = build_pharmacy_graph(self.engine, model=model, rag_tool=rag).invoke(state)
        self.assertEqual(result['outcome'], 'inventory_fallback')
        self.assertLessEqual(result['rag_attempts'], 2)
        self.assertLessEqual(result['inventory_attempts'], 3)

    def test_repeated_unsafe_alternatives_stop_within_budget(self):
        self.seed('演示甲', contraindications='孕妇禁用')
        self.seed('演示乙', contraindications='孕妇禁用')
        state, model = self.state(names=['演示甲'], symptoms=['咽痛'], tags=['孕妇'])
        rag = Rag(['演示甲', '演示乙'])
        result = build_pharmacy_graph(self.engine, model=model, rag_tool=rag).invoke(state)
        self.assertEqual(result['outcome'], 'inventory_fallback')
        self.assertEqual(result['inventory_attempts'], 3)
        self.assertEqual(result['rag_attempts'], 2)
        self.assertEqual(set(result['excluded_drugs']), {'演示甲', '演示乙'})
        self.assertFalse(any(card['type'] == 'drug_candidate' for card in result['ui_cards']))


class CandidateProvenanceTests(unittest.TestCase):
    def service(self, items):
        class Service:
            def search(self, *args, **kwargs):
                return {'items': items}
        return Service()

    def test_knowledge_fallback_cannot_fabricate_citations(self):
        result = {'recommended_drugs': ['演示甲'], 'source': 'llm_knowledge', 'evidence_ids': []}
        with patch.dict('os.environ', {'MED_AGENT_ALLOW_KNOWLEDGE_CANDIDATES': '1'}):
            answer = query_candidates(['测试症状'], [], [], model=Model(result), service=self.service([]))
        self.assertEqual(answer['source'], 'llm_knowledge')
        self.assertEqual(answer['evidence'], [])
        with patch.dict('os.environ', {'MED_AGENT_ALLOW_KNOWLEDGE_CANDIDATES': '0'}), self.assertRaises(ValueError):
            query_candidates(['测试症状'], [], [], model=Model(result), service=self.service([]))

    def test_retrieval_requires_actual_name_and_valid_evidence_id(self):
        items = [{'id': 'e1', 'text': '演示甲：测试段落', 'source': {'title': '合成夹具'}}]
        result = {'recommended_drugs': ['演示甲'], 'source': 'retrieval', 'evidence_ids': ['e1']}
        self.assertEqual(query_candidates(['测试症状'], [], [], model=Model(result), service=self.service(items))['source'], 'retrieval')
        for change in ({'evidence_ids': ['invented']}, {'recommended_drugs': ['未在原文出现的药']}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                query_candidates(['测试症状'], [], [], model=Model({**result, **change}), service=self.service(items))
