"""Durable node recovery, restart ownership, no duplicate turns and fresh stock/safety."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlmodel import Session

from meditouch.api import create_app
from meditouch.chat_storage import ChatStore, ChatBusy, ChatNotFound
from meditouch.checkpoints import checkpoint_saver, thread_config
from meditouch.inventory_models import create_inventory_engine, create_inventory_tables, DrugInventory, DrugDetails
from meditouch.pharmacy_graph import build_pharmacy_graph


class Model:
    def __init__(self): self.intakes = 0
    def complete(self, messages):
        context = json.loads(messages[1]['content'])
        if 'query' in context:
            self.intakes += 1
            value = {'intent': 'find_drug', 'target_drugs': ['演示药'], 'symptoms': [], 'customer_tags': ['成人']}
        else:
            value = {'script_id': next(iter(context['approved_scripts']))}
        return {'role': 'assistant', 'content': json.dumps(value)}


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


class CheckpointRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ChatStore(self.root / 'chats.db')
        self.engine = create_inventory_engine('sqlite:///' + (self.root / 'stock.db').as_posix())
        self.addCleanup(self.engine.dispose)
        create_inventory_tables(self.engine)
        with Session(self.engine) as session:
            drug = DrugInventory(name='演示药', shelf_location='A1', stock=8)
            session.add(drug); session.commit(); session.refresh(drug)
            self.drug_id = drug.id
            session.add(DrugDetails(inventory_id=drug.id, contraindications='孕妇禁用'))
            session.commit()
        self.model = Model()

    def client(self):
        return TestClient(create_app(engine_resolver=lambda store: self.engine, model_factory=lambda: self.model,
                                    chat_store=self.store, health_evidence_loader=lambda *a, **k: ([], [])))

    def begin_failed(self, client):
        chat = client.post('/api/chats', json={'store_id': 'A'}).json()['id']
        body = {'question': '演示药在哪', 'session_id': chat, 'turn_id': 'turn-original',
                'workspace': {'store_id': 'A', 'task': 'drug_lookup', 'customer': {'customer_tags': ['成人']}}}
        def crash(stage):
            if stage == 'validate': raise RuntimeError('Injected process failure after stock lookup')
        def builder(*args, **kwargs):
            return build_pharmacy_graph(*args, **kwargs, on_progress=crash)
        with patch('meditouch.api.build_pharmacy_graph', side_effect=builder):
            first = events(client.post('/api/agent/stream', json=body))
        self.assertEqual(first[-1]['event'], 'error')
        return body

    def test_recovery_across_new_app_uses_fresh_stock_and_not_another_intake(self):
        with self.client() as client:
            body = self.begin_failed(client)
        # Change the underlying business facts between executions.
        with Session(self.engine) as session:
            drug = session.get(DrugInventory, self.drug_id)
            drug.stock, drug.shelf_location = 2, 'B9'
            session.add(drug); session.commit()
        with self.client() as restarted:
            resumed = events(restarted.post('/api/agent/stream', json={**body, 'resume': True, 'question': '伪造的新问题'}))
            self.assertEqual(resumed[-1]['event'], 'done')
            final = [e['payload'] for e in resumed if e['event'] == 'cards.snapshot'][-1]
            drug = next(c['drug'] for c in final['cards'] if c['type'] == 'drug_candidate')
            self.assertEqual((drug['stock'], drug['shelf_location']), (2, 'B9'))
            self.assertEqual(self.model.intakes, 1)
            saved = restarted.get(f"/api/chats/{body['session_id']}", params={'store_id': 'A'}).json()
            self.assertEqual(len(saved['turns']), 1)
            self.assertEqual(saved['turns'][0]['question'], body['question'])
            self.assertTrue(saved['turns'][0]['completed'])
            self.assertEqual(restarted.post('/api/agent/stream', json={**body, 'resume': True}).status_code, 409)

    def test_changed_contraindication_is_rechecked_and_old_candidate_removed(self):
        with self.client() as client:
            body = self.begin_failed(client)
            with Session(self.engine) as session:
                drug = session.get(DrugInventory, self.drug_id)
                drug.details.contraindications = '成人禁用'
                session.add(drug.details); session.commit()
            resumed = events(client.post('/api/agent/stream', json={**body, 'resume': True}))
            self.assertEqual(resumed[-1]['event'], 'done')
            cards = [e['payload']['cards'] for e in resumed if e['event'] == 'cards.snapshot'][-1]
            self.assertFalse(any(c['type'] == 'drug_candidate' for c in cards))
            self.assertTrue(any(c['type'] == 'intercept_warning' for c in cards))

    def test_checkpoint_is_scoped_and_saved_to_disk(self):
        with self.client() as client:
            body = self.begin_failed(client)
        path = self.store.path.with_suffix('.checkpoints.sqlite')
        with checkpoint_saver(path) as saver:
            graph = build_pharmacy_graph(self.engine, model=self.model, checkpointer=saver)
            correct = thread_config('local-demo', 'A', body['session_id'], body['turn_id'])
            self.assertEqual(graph.get_state(correct).next, ('safety_node',))
            self.assertEqual(graph.get_state(correct).values['inventory_results'][0]['stock'], 8)
            other = thread_config('other-owner', 'A', body['session_id'], body['turn_id'])
            self.assertFalse(graph.get_state(other).values)

    def test_store_claims_prevent_concurrent_or_cross_scope_resume_and_limit_attempts(self):
        chat = self.store.create('owner', 'A')['id']
        self.store.begin_turn(chat, 'owner', 'A', 't1', 'question', {})
        with self.assertRaises(ChatBusy): self.store.resume_turn(chat, 'owner', 'A', 't1')
        self.store.interrupt_turn(chat, 'owner', 'A', 't1')
        with self.assertRaises(ChatNotFound): self.store.resume_turn(chat, 'other', 'A', 't1')
        for _ in range(3):
            self.store.resume_turn(chat, 'owner', 'A', 't1')
            self.store.interrupt_turn(chat, 'owner', 'A', 't1')
        with self.assertRaises(ChatBusy): self.store.resume_turn(chat, 'owner', 'A', 't1')

    def test_server_restart_marks_prior_processing_as_interrupted(self):
        chat = self.store.create('owner', 'A')['id']
        self.store.begin_turn(chat, 'owner', 'A', 't1', 'question', {})
        with patch('meditouch.chat_storage.PROCESS_TOKEN', 'different-process'):
            self.assertEqual(self.store.get(chat, 'owner', 'A')['turns'][0]['status'], 'interrupted')
            self.store.resume_turn(chat, 'owner', 'A', 't1')

    def test_completed_health_node_is_not_repeated_when_resuming_inventory(self):
        class HealthModel(Model):
            health_calls = 0
            def complete(self, messages):
                value = json.loads(messages[1]['content'])
                if 'source_mode' in value:
                    self.health_calls += 1
                    return {'role': 'assistant', 'content': json.dumps({'sections': [
                        {'title': '一般说明', 'items': [{'text': '这是合成解释。', 'citations': [], 'highlights': []}]}],
                        'follow_up': None})}
                response = super().complete(messages)
                if 'query' in value:
                    response['content'] = json.dumps({**json.loads(response['content']), 'needs_health_answer': True})
                return response
        self.model = HealthModel()
        with self.client() as client:
            body = self.begin_failed(client)
            self.assertEqual(self.model.health_calls, 1)
            result = events(client.post('/api/agent/stream', json={**body, 'resume': True}))
            self.assertEqual(result[-1]['event'], 'done')
            self.assertEqual(self.model.health_calls, 1)
            cards = [e['payload']['cards'] for e in result if e['event'] == 'cards.snapshot'][-1]
            self.assertTrue(any(c['type'] == 'health_answer' for c in cards))

    def test_older_turn_cannot_resume_after_a_new_turn_started(self):
        chat = self.store.create('owner', 'A')['id']
        self.store.begin_turn(chat, 'owner', 'A', 't1', 'first', {})
        self.store.interrupt_turn(chat, 'owner', 'A', 't1')
        self.store.begin_turn(chat, 'owner', 'A', 't2', 'second', {})
        self.store.interrupt_turn(chat, 'owner', 'A', 't2')
        with self.assertRaises(ChatBusy): self.store.resume_turn(chat, 'owner', 'A', 't1')


if __name__ == '__main__': unittest.main()
