"""B-side role/state contracts, isolated fixtures; no dispensing validation claims."""
import importlib.util
import json
import unittest

from meditouch.pharmacy_context import initial_workflow, validate_workspace


class WorkspaceTests(unittest.TestCase):
    def test_declarations_do_not_grant_approval(self):
        for forged in ('inventory', 'safety', 'handoff', 'sale_authorized'):
            with self.subTest(field=forged), self.assertRaises(ValueError):
                validate_workspace({'store_id': 'store-A', forged: 'passed'})
        with self.assertRaises(ValueError):
            validate_workspace({'store_id': 'store-A', 'customer': {'approved': 'yes'}})

    def test_unknown_is_preserved_and_instances_are_isolated(self):
        value = {'store_id': ' A ', 'customer': {'allergies': ' 不清楚 '}}
        workspace = validate_workspace(value)
        self.assertEqual(workspace['customer'], {'allergies': '不清楚'})
        self.assertEqual(value['customer']['allergies'], ' 不清楚 ')
        first, second = initial_workflow(), initial_workflow()
        first['inventory']['items'].append({'sku': 'fixture'})
        self.assertEqual(second['inventory']['items'], [])


@unittest.skipUnless(importlib.util.find_spec('langgraph'), '需要 requirements-agent.txt')
class PharmacyGraphTests(unittest.TestCase):
    def test_workspace_reaches_model_and_result_without_patient_intake(self):
        from meditouch.agent_graph import run_graph
        from scripts.agent_acceptance import ScriptedModel, fixture_service

        class RecordingModel(ScriptedModel):
            def complete(self, messages, *args, **kwargs):
                self.initial = messages[:2]
                return super().complete(messages, *args, **kwargs)

        model = RecordingModel('normal')
        result = run_graph('演示措施X的原文规则是什么？', corpus='demo',
                           workspace={'store_id': 'store-A', 'task': 'coaching',
                                      'customer': {'age': '30岁', 'allergies': '不清楚'}},
                           model=model, service=fixture_service())
        self.assertEqual(result['status'], 'answered')
        self.assertIn('实体药店实习生 Copilot', model.initial[0]['content'])
        payload = json.loads(model.initial[1]['content'])
        self.assertEqual(payload['workspace']['customer']['allergies'], '不清楚')
        self.assertEqual(payload['missing_context'], [])
        self.assertEqual(result['flow']['kind'], 'pharmacy_copilot')
        state = result['pharmacy_workflow']
        self.assertEqual(state['inventory']['status'], 'not_connected')
        self.assertEqual(state['safety']['status'], 'not_verified')
        self.assertFalse(state['handoff']['sale_authorized'])
        self.assertEqual(state['teaching']['evidence_ids'],
                         [c['evidence_id'] for c in result['citations']])
        self.assertIn('medications', state['safety']['missing'])

    def test_missing_store_and_mixed_patient_context_are_rejected(self):
        from meditouch.agent_graph import run_graph
        for kwargs in ({'workspace': {}},
                       {'workspace': {'store_id': 'A'}, 'known_context': {'population': '成人'}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_graph('药品知识查询', **kwargs)

    def test_model_failure_still_retains_handoff_and_no_clearance(self):
        from meditouch.agent_graph import run_graph
        from scripts.agent_acceptance import fixture_service

        class BrokenModel:
            def complete(self, *args, **kwargs):
                raise RuntimeError('private provider detail')

        result = run_graph('演示措施X', corpus='demo', workspace={'store_id': 'A'},
                           model=BrokenModel(), service=fixture_service())
        self.assertEqual(result['status'], 'model_error')
        self.assertFalse(result['pharmacy_workflow']['handoff']['sale_authorized'])
        self.assertNotIn('private provider detail', json.dumps(result))

    def test_risk_interrupt_does_not_wait_for_retrieval(self):
        from meditouch.agent_graph import run_graph
        result = run_graph('我现在呼吸困难', workspace={'store_id': 'A'})
        self.assertEqual(result['status'], 'risk_alert')
        self.assertEqual(result['tool_calls'], 0)
        self.assertTrue(result['pharmacy_workflow']['handoff']['required'])
