"""Keep the pharmacy entry point runnable without importing the legacy C-side stack."""
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

from meditouch.pharmacy_state import PharmacyState
from meditouch.risk import risk_matches, stopped_result


class PharmacyDependencyTests(unittest.TestCase):
    def test_pharmacy_api_and_graph_run_with_legacy_imports_blocked(self):
        # A fresh interpreter catches transitive imports hidden by unittest's module cache.
        program = textwrap.dedent('''
            import importlib.abc
            import json
            import sys

            blocked = {
                'meditouch.agent_graph', 'meditouch.consultation',
                'meditouch.consultation_flow', 'meditouch.patient_context',
                'meditouch.respiratory_guidance', 'meditouch.diarrhea_guidance',
                'meditouch.symptom_clarification', 'meditouch.query_plan',
                'meditouch.evidence_service', 'meditouch.server',
            }
            class BlockLegacy(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname in blocked:
                        raise ImportError('Forbidden legacy dependency: ' + fullname)
                    return None
            sys.meta_path.insert(0, BlockLegacy())

            from fastapi.testclient import TestClient
            from meditouch.api import create_app
            from meditouch.inventory_models import create_inventory_engine, create_inventory_tables
            from meditouch.pharmacy_graph import build_pharmacy_graph, prepare_inventory_state

            class OfflineModel:
                def complete(self, *args, **kwargs):
                    raise AssertionError('No model call expected')

            engine = create_inventory_engine('sqlite://')
            try:
                create_inventory_tables(engine)
                workspace = {'store_id': 'test', 'task': 'drug_lookup',
                             'customer': {'requested_drug': '未收录商品', 'customer_tags': []}}
                state = prepare_inventory_state('查询未收录商品', workspace, model=OfflineModel())
                result = build_pharmacy_graph(engine, model=OfflineModel()).invoke(state)
                assert result['outcome'] == 'inventory_fallback'
                assert result['safety_status'] == 'unknown'
                app = create_app(engine_resolver=lambda store: engine, model_factory=OfflineModel)
                with TestClient(app) as client:
                    response = client.post('/api/agent/stream', json={
                        'question': '顾客现在呼吸困难', 'workspace': workspace})
                    assert response.status_code == 200
                    frames = [json.loads(line[6:]) for line in response.text.splitlines()
                              if line.startswith('data: ')]
                    snapshot = next(frame for frame in frames if frame['event'] == 'cards.snapshot')
                    assert snapshot['payload']['cards'][0]['type'] == 'intercept_warning'
                    assert frames[-1]['event'] == 'done'
                assert not blocked.intersection(sys.modules)
            finally:
                engine.dispose()
            print('isolated pharmacy workflow passed')
        ''')
        result = subprocess.run([sys.executable, '-B', '-c', program],
                                cwd=Path(__file__).resolve().parent.parent,
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pharmacy_schema_keeps_rag_fields_without_evidence_graph_fields(self):
        self.assertTrue({'rag_recommended_drugs', 'has_tried_rag', 'rag_status',
                         'safety_status', 'inventory_results', 'trace'}.issubset(
                             PharmacyState.__annotations__))
        self.assertTrue({'messages', 'evidence', 'draft', 'validation'}.isdisjoint(
            PharmacyState.__annotations__))

    def test_legacy_risk_exports_share_the_same_implementation(self):
        from meditouch import consultation_flow
        self.assertIs(consultation_flow.risk_matches, risk_matches)
        self.assertIs(consultation_flow.stopped_result, stopped_result)

    def test_shared_risk_preserves_current_negated_historical_and_context_cases(self):
        self.assertEqual(risk_matches('顾客现在呼吸困难', {})[0]['level'], 'emergency')
        self.assertEqual(risk_matches('顾客高烧不退', {})[0]['level'], 'prompt_care')
        for question in ('顾客没有呼吸困难', '如果顾客呼吸困难怎么办',
                         '顾客去年呼吸困难，已经缓解了', '呼吸困难是什么意思？'):
            with self.subTest(question=question):
                self.assertEqual(risk_matches(question, {}), [])
        matches = risk_matches('核对顾客情况', {'associated_symptoms': '现在喘不过气'})
        self.assertEqual(matches[0]['field'], 'associated_symptoms')
        for level in ('emergency', 'prompt_care'):
            with self.subTest(level=level):
                result = stopped_result('顾客不适', {'kind': 'risk', 'risk_level': level})
                self.assertEqual(result['status'], 'risk_alert')
                self.assertEqual(result['model_calls'], 0)
                self.assertEqual(result['tool_calls'], 0)
                self.assertEqual(result['citations'], [])
                self.assertTrue(result['risk_references'])
                self.assertIn('不能据此确认病因或严重程度', result['answer'])
