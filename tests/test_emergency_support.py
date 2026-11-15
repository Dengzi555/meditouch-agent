"""Engineering acceptance of first-aid conditions, not clinical validation of a triage model."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from meditouch.api import create_app
from meditouch.chat_storage import ChatStore
from meditouch.emergency_support import build_emergency_card
from meditouch.pharmacy_graph import build_pharmacy_graph, prepare_inventory_state
from meditouch.risk import risk_matches


class EmergencySupportTests(unittest.TestCase):
    def test_unknown_never_inferred_from_question_and_advice_not_recorded_as_done(self):
        card = build_emergency_card('顾客现在叫不醒，呼吸困难怎么办')
        self.assertEqual(card['facts']['consciousness'], 'unknown')
        self.assertEqual(card['facts']['breathing'], 'unknown')
        self.assertIn('已实际采取的措施：未确认', card['handover'])
        self.assertIn('最早出现症状时间：未确认', card['handover'])
        self.assertIn('若呼唤无反应且没有正常呼吸', '\n'.join(card['actions']))
        self.assertNotIn('目前记录符合该条件', '\n'.join(card['actions']))

    def test_cpr_confirmation_requires_both_conditions(self):
        for response, breath, cpr, recovery in [
            ('unresponsive', 'absent_or_gasping', True, False),
            ('unresponsive', 'abnormal', True, False),
            ('unresponsive', 'normal', False, True),
            ('unresponsive', 'unknown', False, False),
            ('responsive', 'abnormal', False, False),
        ]:
            card = build_emergency_card('顾客呼吸困难怎么办', changes={'consciousness': response, 'breathing': breath})
            self.assertEqual(card['actions'][2].startswith('目前记录符合'), cpr)
            self.assertEqual(card['actions'][3].startswith('目前记录符合'), recovery)

    def test_stroke_keeps_uncertainty_and_no_oral_medication(self):
        card = build_emergency_card('顾客现在嘴歪、言语不清，是脑出血吗？')
        actions = '\n'.join(card['actions'])
        self.assertEqual(card['severity'], 'emergency')
        self.assertIn('不能在店内区分脑梗与脑出血', actions)
        self.assertIn('包括阿司匹林', actions)
        self.assertIn('原始描述：', card['handover'])
        self.assertNotIn('确诊脑出血', card['handover'])

    def test_negated_historical_and_educational_signs_not_emergencies(self):
        for question in ['顾客没有言语不清', '我以前嘴歪过', '如果顾客言语不清怎么办', '科普口角歪斜是什么意思',
                         '我没有呼吸困难，只是咳嗽', '没有呼吸困难怎么办？']:
            self.assertFalse(risk_matches(question, {}), question)

    def test_prompt_care_not_automatically_ambulance_and_can_escalate(self):
        first = build_emergency_card('顾客高烧不退怎么办')
        self.assertEqual(first['severity'], 'prompt_care')
        next_card = build_emergency_card(first['original_query'], previous=first, changes={'consciousness': 'unresponsive'})
        self.assertEqual(next_card['severity'], 'emergency')
        self.assertIn('立即拨打120', next_card['actions'][0])

    def test_graph_does_not_call_model_inventory_or_rag(self):
        class Forbidden:
            def complete(self, *_): raise AssertionError('Model must not be called')
            def invoke(self, *_): raise AssertionError('RAG must not be called')
        def tools(*a, **kw): raise AssertionError('Inventory tools must not be initialized')
        model = Forbidden()
        initial = prepare_inventory_state('顾客现在嘴歪、言语不清怎么办',
            {'store_id': 'A', 'task': 'drug_lookup', 'customer': {}}, model=model)
        state = build_pharmacy_graph(None, model=model, tool_factory=tools, rag_tool=model).invoke(initial)
        self.assertEqual(state['outcome'], 'risk_alert')
        self.assertEqual(state['inventory_results'], [])
        self.assertEqual(state['tool_calls'], 0)
        self.assertEqual(state['ui_cards'][0]['type'], 'emergency_support')


class EmergencyApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ChatStore(Path(self.temp.name) / 'chat.db')
        self.addCleanup(patch.stopall)
        patch.dict('os.environ', {'MED_AGENT_INVENTORY_STORE_ID': 'A'}).start()
        self.client = TestClient(create_app(chat_store=self.store, identity_resolver=lambda req: req.headers.get('x-owner', 'owner')))
        self.addCleanup(self.client.close)
        self.session = self.store.create('owner', 'A')['id']
        self.store.begin_turn(self.session, 'owner', 'A', 't1', '顾客现在意识不清怎么办', {})
        self.first = build_emergency_card('顾客现在意识不清怎么办')
        self.store.finish_turn(self.session, 'owner', 'A', 't1',
                               {'ui_cards': [self.first], 'safety_status': 'unsafe', 'answer': 'test'}, [])
        self.url = f'/api/chats/{self.session}/turns/t1/emergency'

    def update(self, changes, revision=0, **kwargs):
        return self.client.post(self.url, json={'store_id': 'A', 'expected_revision': revision, 'changes': changes}, **kwargs)

    def test_patch_persists_same_turn_and_handover_and_revision(self):
        response = self.update({'call_status': 'called', 'consciousness': 'unresponsive', 'breathing': 'normal',
                                'onset': '约10分钟前', 'actions_taken': '已按120指令采取侧卧位'})
        self.assertEqual(response.status_code, 200, response.text)
        card = response.json()
        self.assertEqual(card['revision'], 1)
        self.assertIn('约10分钟前', card['handover'])
        self.assertIn('店员确认已拨打120', card['handover'])
        saved = ChatStore(self.store.path).get(self.session, 'owner', 'A')
        self.assertEqual(len(saved['turns']), 1)
        self.assertEqual(saved['turns'][0]['cards'][0]['handover'], card['handover'])
        self.assertEqual(len(card['updates']), 1)
        self.assertEqual(self.update({'breathing': 'normal'}).status_code, 409)
        second = self.update({'breathing': 'abnormal'}, revision=1).json()
        self.assertEqual(second['facts']['onset'], '约10分钟前')
        self.assertEqual(second['revision'], 2)

    def test_scope_origin_and_untrusted_fields(self):
        self.assertEqual(self.update({}, headers={'x-owner': 'other'}).status_code, 404)
        self.assertEqual(self.update({}, headers={'origin': 'https://other.example'}).status_code, 403)
        for changes in [{'diagnosis': '脑出血'}, {'breathing': 'safe'}, {'call_status': None},
                        {'age': 12}, {'actions_taken': 'x' * 501}]:
            self.assertEqual(self.update(changes).status_code, 422, changes)

    def test_only_latest_completed_emergency_is_editable(self):
        self.store.begin_turn(self.session, 'owner', 'A', 't2', '另一个问题', {})
        self.assertEqual(self.update({'breathing': 'normal'}).status_code, 409)
        self.store.finish_turn(self.session, 'owner', 'A', 't2', {}, [])
        self.assertEqual(self.update({}).status_code, 409)
        response = self.client.post(f'/api/chats/{self.session}/turns/t2/emergency',
            json={'store_id': 'A', 'expected_revision': 0, 'changes': {}})
        self.assertEqual(response.status_code, 409)

    def test_timeline_is_bounded_and_unknown_can_replace_observation(self):
        card = self.first
        for _ in range(35):
            card = build_emergency_card(card['original_query'], previous=card, changes={'breathing': 'normal'})
        self.assertEqual(len(card['updates']), 30)
        card = build_emergency_card(card['original_query'], previous=card, changes={'breathing': 'unknown'})
        self.assertIn('呼吸：呼吸未确认', card['handover'])


if __name__ == '__main__':
    unittest.main()
