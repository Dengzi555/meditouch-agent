"""Mandatory safety edges and bounded replacement lookup; synthetic stock only."""
from functools import partial
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlmodel import Session

from meditouch.inventory_models import DrugDetails, DrugInventory, create_inventory_engine, create_inventory_tables
from meditouch.inventory_tools import build_inventory_tools, build_medical_rag_tool
from meditouch.pharmacy_graph import build_pharmacy_graph, inventory_engine_for_store, prepare_entities, route_after_safety


def initial(**changes):
    return dict(user_query='演示查询', extracted_entities={'drug_name': '演示'},
                customer_tags=['标签甲'], inventory_results=[], safety_status='unknown',
                safety_reason='未核查', excluded_drugs=[], inventory_attempts=0,
                tool_calls=0, trace=[], **changes)


class PharmacyGraphTests(unittest.TestCase):
    def symptom_state(self, symptom='感冒'):
        state = initial()
        state.update(extracted_entities={'symptom': symptom}, has_tried_rag=False,
                     rag_recommended_drugs=[])
        return state

    def test_rag_tool_accepts_list_context(self):
        with patch('meditouch.medical_candidates.query_candidates', return_value={'recommended_drugs': []}) as query:
            result = build_medical_rag_tool().invoke({'symptoms': ['咽痛', '流鼻涕'], 'customer_tags': ['孕妇']})
            self.assertEqual(result['recommended_drugs'], [])
            self.assertEqual(query.call_args.args[:2], (['咽痛', '流鼻涕'], ['孕妇']))

    def test_rag_candidates_use_exact_names_and_mandatory_safety(self):
        self.seed('复方感冒灵颗粒')  # Must not be silently substituted for 感冒灵颗粒.
        self.seed('板蓝根颗粒')
        state = build_pharmacy_graph(self.engine).invoke(self.symptom_state())
        self.assertTrue(state['has_tried_rag'])
        self.assertEqual(state['rag_recommended_drugs'], ['感冒灵颗粒', '板蓝根颗粒'])
        self.assertEqual([item['name'] for item in state['inventory_results']], ['板蓝根颗粒'])
        self.assertEqual([item['name'] for item in state['safety_checks']], ['板蓝根颗粒'])
        self.assertEqual([item['node'] for item in state['trace']], [
            'inventory_node', 'rag_node', 'inventory_node', 'safety_node', 'generator_node'])
        self.assertEqual(state['safety_status'], 'unknown')
        self.assertEqual(state['tool_calls'], 5)  # First lookup, RAG, two names, one safety check.
        self.assertTrue(any(card['id'] == 'knowledge-source' for card in state['ui_cards']))

    def test_rag_unsafe_candidate_excluded_and_not_reintroduced(self):
        self.seed('感冒灵颗粒', '标签甲禁用')
        self.seed('板蓝根颗粒')
        state = build_pharmacy_graph(self.engine).invoke(self.symptom_state())
        self.assertEqual(state['excluded_drugs'], ['感冒灵颗粒'])
        self.assertEqual(state['inventory_attempts'], 3)
        self.assertEqual([item['name'] for item in state['inventory_results']], ['板蓝根颗粒'])
        self.assertEqual(sum(item['node'] == 'rag_node' for item in state['trace']), 1)

    def test_rag_empty_error_or_no_exact_stock_terminates(self):
        class BrokenRag:
            def invoke(self, args):
                raise RuntimeError('test failure')
        for symptom, tool, status in [('未知主题', None, 'not_found'),
                                      ('感冒', BrokenRag(), 'tool_error'), ('感冒', None, 'ok')]:
            with self.subTest(symptom=symptom, status=status):
                state = build_pharmacy_graph(self.engine, rag_tool=tool).invoke(self.symptom_state(symptom))
                self.assertEqual(state['outcome'], 'inventory_fallback')
                self.assertEqual(state['rag_status'], status)
                self.assertEqual(state['inventory_attempts'], 2)
                self.assertEqual(sum(item['node'] == 'rag_node' for item in state['trace']), 1)

    def test_no_rag_for_explicit_drug_or_database_failure(self):
        class NeverRag:
            def invoke(self, args):
                raise AssertionError('RAG must not run')
        state = build_pharmacy_graph(self.engine, rag_tool=NeverRag()).invoke(initial())
        self.assertFalse(state.get('has_tried_rag', False))
        class Tool:
            def invoke(self, args):
                return {'status': 'database_unavailable', 'items': []}
        state = build_pharmacy_graph(self.engine, rag_tool=NeverRag(),
            tool_factory=lambda *args, **kwargs: (Tool(), Tool())).invoke(self.symptom_state())
        self.assertFalse(state['has_tried_rag'])
        self.assertEqual(state['inventory_attempts'], 1)

    def setUp(self):
        class FixtureRag:
            def invoke(self, args):
                return ['感冒灵颗粒', '板蓝根颗粒'] if '感冒' in args['symptoms'] else []
        rag_patch = patch('meditouch.pharmacy_graph.build_medical_rag_tool', return_value=FixtureRag())
        rag_patch.start()
        self.addCleanup(rag_patch.stop)
        class StaffModel:
            def complete(self, messages):
                data = json.loads(messages[1]['content'])
                if 'query' in data:
                    return {'role': 'assistant', 'content': json.dumps({'intent': 'find_drug',
                        'symptoms': [], 'target_drugs': [data['customer']['requested_drug']],
                        'customer_tags': data['customer'].get('customer_tags', [])})}
                allowed = data['approved_scripts']
                return {'role': 'assistant', 'content': json.dumps({'script_id': next(iter(allowed))})}
        model_patch = patch('meditouch.pharmacy_generation.ModelGateway', return_value=StaffModel())
        self.model = StaffModel()
        model_patch.start()
        self.addCleanup(model_patch.stop)
        self.engine = create_inventory_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        create_inventory_tables(self.engine)

    def seed(self, name, contraindications=None, stock=1):
        with Session(self.engine) as session:
            drug = DrugInventory(name=name, stock=stock, shelf_location='A1')
            session.add(drug)
            session.commit()
            session.refresh(drug)
            if contraindications is not None:
                session.add(DrugDetails(inventory_id=drug.id, contraindications=contraindications))
                session.commit()

    def test_unsafe_is_excluded_then_unknown_requires_manual_review(self):
        self.seed('演示甲', '标签甲禁用')
        self.seed('演示乙')
        state = build_pharmacy_graph(self.engine).invoke(initial())
        self.assertEqual(state['excluded_drugs'], ['演示甲'])
        self.assertEqual(state['inventory_attempts'], 2)
        self.assertEqual([item['name'] for item in state['inventory_results']], ['演示乙'])
        self.assertEqual(state['safety_status'], 'unknown')
        self.assertIn('请人工核对病史', state['answer'])
        self.assertEqual([event['node'] for event in state['trace']], [
            'inventory_node', 'safety_node', 'inventory_node', 'safety_node', 'generator_node'])

    def test_all_unsafe_or_no_candidates_fall_back(self):
        self.seed('演示甲', '标签甲禁用')
        state = build_pharmacy_graph(self.engine).invoke(initial())
        self.assertEqual(state['outcome'], 'inventory_fallback')
        self.assertEqual(state['inventory_attempts'], 2)
        self.assertEqual(state['inventory_results'], [])
        self.assertEqual(state['safety_status'], 'unsafe')

    def test_attempt_limit_does_not_depend_on_exclusion_count(self):
        state = initial()
        state.update(inventory_results=[{'name': '演示'}], safety_status='unsafe',
                     excluded_drugs=['演示'], inventory_attempts=3)
        self.assertEqual(route_after_safety(state), 'fallback_node')

    def test_queries_are_bounded_and_engine_is_bound_to_both_tools(self):
        engines = []

        class FakeTool:
            def __init__(self, callback):
                self.callback = callback

            def invoke(self, args):
                return self.callback(args)

        def factory(engine, *, excluded_drugs=()):
            engines.append(engine)
            index = len(excluded_drugs) + 1
            return FakeTool(lambda args: {'status': 'ok', 'items': [
                {'inventory_id': index, 'name': f'演示{index}', 'stock': 1, 'shelf_location': 'A'}
            ]}), FakeTool(lambda args: {'is_safe': False, 'reason': '标签甲禁用'})

        state = build_pharmacy_graph(self.engine, tool_factory=factory).invoke(initial())
        self.assertEqual(state['inventory_attempts'], 3)
        self.assertEqual(state['outcome'], 'inventory_fallback')
        self.assertEqual(len(state['excluded_drugs']), 3)
        self.assertTrue(all(engine is self.engine for engine in engines))

    def test_unknown_dominates_true_and_safe_edge_requires_explicit_true(self):
        self.seed('演示甲')
        self.seed('演示乙')

        def factory(engine, **kwargs):
            search, safety = build_inventory_tools(engine, **kwargs)
            class Safety:
                def invoke(self, args):
                    response = safety.invoke(args)
                    if args['drug_name'] == '演示甲' or allow_all:
                        response['is_safe'] = True
                    return response
            return search, Safety()

        allow_all = False
        graph = build_pharmacy_graph(self.engine, tool_factory=factory)
        state = graph.invoke(initial())
        self.assertEqual(state['safety_status'], 'unknown')
        allow_all = True
        state = graph.invoke(initial())
        self.assertEqual(state['safety_status'], 'safe')
        self.assertEqual(state['outcome'], 'inventory_candidates')

    def test_symptom_entities_are_used_and_zero_stock_is_not_recommended(self):
        self.seed('演示成分', stock=0)
        factory = partial(build_inventory_tools, symptom_terms={'演示症状': ['演示成分']})
        state = initial()
        state['extracted_entities'] = {'symptom': '演示症状'}
        result = build_pharmacy_graph(self.engine, tool_factory=factory).invoke(state)
        self.assertIn('缺货，不可作为可售候选', result['answer'])
        self.assertIn('请人工核对病史', result['answer'])

    def test_existing_run_graph_dispatches_to_inventory_flow(self):
        from meditouch.agent_graph import run_graph
        self.seed('演示甲')
        result = run_graph('帮我找演示甲', inventory_engine=self.engine, model=self.model,
                           workspace={'store_id': 'A', 'task': 'drug_lookup',
                                      'customer': {'requested_drug': '演示甲', 'customer_tags': ['标签甲']}})
        self.assertEqual(result['status'], 'inventory_candidates')
        self.assertEqual(result['model_calls'], 2)
        self.assertEqual(result['pharmacy_workflow']['safety']['status'], 'unknown')
        self.assertFalse(result['pharmacy_workflow']['handoff']['sale_authorized'])

    def test_model_extracts_query_without_inventing_drug(self):
        class Model:
            def complete(self, *args):
                return {'role': 'assistant', 'content': json.dumps({
                    'drug_name': '演示甲', 'customer_tags': ['标签甲']})}
        intake, status, calls = prepare_entities('标签甲，查演示甲', {}, Model())
        self.assertEqual(intake['target_drugs'], ['演示甲'])
        self.assertEqual(intake['customer_tags'], ['标签甲'])
        self.assertEqual(status, 'ok')
        self.assertEqual(calls, 1)
        _, status, _ = prepare_entities('另一个问题', {}, Model())
        self.assertEqual(status, 'unavailable')

    def test_declared_tags_remain_valid_when_model_echoes_them(self):
        class Model:
            def complete(self, messages):
                return {'role': 'assistant', 'content': '{"symptom":"发热","customer_tags":["孕妇"]}'}
        intake, status, _ = prepare_entities('发烧', {'customer_tags': ['孕妇']}, Model())
        self.assertEqual(intake['symptoms'], ['发热'])
        self.assertEqual(intake['customer_tags'], ['孕妇'])
        self.assertEqual(status, 'ok')

    def test_host_binding_is_single_store_and_readonly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stock.db'
            engine = create_inventory_engine('sqlite:///' + path.as_posix())
            create_inventory_tables(engine)
            engine.dispose()
            with patch.dict('os.environ', {'MED_AGENT_INVENTORY_DB': str(path),
                                          'MED_AGENT_INVENTORY_STORE_ID': 'A'}):
                with self.assertRaises(ValueError):
                    inventory_engine_for_store('B')
                readonly = inventory_engine_for_store('A')
                try:
                    with readonly.connect() as connection:
                        from sqlalchemy.exc import OperationalError
                        with self.assertRaises(OperationalError):
                            connection.exec_driver_sql("INSERT INTO drug_inventory (name, shelf_location, created_at) VALUES ('x','A','2026-11-13')")
                finally:
                    readonly.dispose()
