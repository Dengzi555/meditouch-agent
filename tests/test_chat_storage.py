"""Persistence, tenant predicates, duplicate requests and incomplete-stream semantics."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlmodel import Session

from meditouch.api import create_app
from meditouch.chat_storage import ChatStore, ChatBusy, ChatNotFound, STALE_SECONDS
from meditouch.inventory_models import DrugInventory, create_inventory_engine, create_inventory_tables


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'chat.db'
        self.store = ChatStore(self.path)
        self.id = self.store.create('u1', 's1')['id']

    def test_reload_and_server_history_keep_cards_and_customer_context(self):
        self.assertEqual(self.store.begin_turn(self.id, 'u1', 's1', 't1', '第一问', {'customer_tags': ['孕妇']}), [])
        self.store.finish_turn(self.id, 'u1', 's1', 't1', {
            'answer': '展示答案', 'safety_status': 'unknown', 'messages': ['PRIVATE_PROMPT'],
            'ui_cards': [{'id': 'a', 'type': 'notice', 'title': '回答', 'message': '已完成'}],
            'symptoms': ['头痛']}, ['库存已查'])
        restored = ChatStore(self.path)
        saved = restored.get(self.id, 'u1', 's1')
        self.assertTrue(saved['turns'][0]['completed'])
        self.assertEqual(saved['turns'][0]['cards'][0]['message'], '已完成')
        history = restored.begin_turn(self.id, 'u1', 's1', 't2', '第二问', {})
        self.assertIn('第一问', history[0]['content'])
        self.assertIn('孕妇', history[0]['content'])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pharmacy_chat_messages').fetchone()[0], 4)
            self.assertNotIn('PRIVATE_PROMPT', str(db.execute('SELECT payload FROM pharmacy_chat_messages').fetchall()))

    def test_all_reads_and_writes_enforce_owner_and_store(self):
        for owner, store in [('u2', 's1'), ('u1', 's2')]:
            with self.subTest(owner=owner, store=store):
                self.assertEqual(self.store.list_sessions(owner, store), [])
                with self.assertRaises(ChatNotFound):
                    self.store.get(self.id, owner, store)
                with self.assertRaises(ChatNotFound):
                    self.store.begin_turn(self.id, owner, store, 't1', 'forged', {})
                with self.assertRaises(ChatNotFound):
                    self.store.interrupt_turn(self.id, owner, store, 't1')

    def test_duplicate_or_parallel_turn_does_not_insert_twice(self):
        self.store.begin_turn(self.id, 'u1', 's1', 't1', 'first', {})
        for turn in ('t1', 't2'):
            with self.assertRaises(ChatBusy):
                self.store.begin_turn(self.id, 'u1', 's1', turn, 'again', {})
        self.store.finish_turn(self.id, 'u1', 's1', 't1', {'ui_cards': []}, [])
        self.store.interrupt_turn(self.id, 'u1', 's1', 't1')
        self.assertTrue(self.store.get(self.id, 'u1', 's1')['turns'][0]['completed'])
        self.store.begin_turn(self.id, 'u1', 's1', 't2', 'next', {})
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pharmacy_chat_messages').fetchone()[0], 4)

    def test_interrupted_and_stale_pending_never_restore_as_complete(self):
        self.store.begin_turn(self.id, 'u1', 's1', 't1', 'first', {})
        self.store.interrupt_turn(self.id, 'u1', 's1', 't1')
        self.store.begin_turn(self.id, 'u1', 's1', 't2', 'second', {})
        with patch('meditouch.chat_storage.time.time', return_value=time.time() + STALE_SECONDS + 1):
            turns = self.store.get(self.id, 'u1', 's1')['turns']
        self.assertTrue(all(not turn['completed'] and not turn['loading'] and not turn['cards'] for turn in turns))
        self.assertTrue(all(turn['status'] == 'interrupted' for turn in turns))


class ApiPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ChatStore(Path(self.temp.name) / 'chat.db')
        self.engine = create_inventory_engine('sqlite:///' + (Path(self.temp.name) / 'stock.db').as_posix())
        create_inventory_tables(self.engine)
        self.addCleanup(self.engine.dispose)
        with Session(self.engine) as session:
            session.add(DrugInventory(name='演示药', stock=3, shelf_location='A1'))
            session.commit()
        self.owner = 'u1'
        self.requests = []
        test = self
        class Model:
            def complete(self, messages):
                data = json.loads(messages[1]['content'])
                if 'query' in data:
                    test.requests.append(data)
                    draft = {'intent': 'find_drug', 'symptoms': [], 'target_drugs': ['演示药'], 'customer_tags': []}
                else:
                    draft = {'script_id': next(iter(data['approved_scripts']))}
                return {'role': 'assistant', 'content': json.dumps(draft)}
        self.app = create_app(engine_resolver=lambda store: self.engine, model_factory=Model,
            chat_store=self.store, identity_resolver=lambda request: self.owner)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.id = self.client.post('/api/chats', json={'store_id': 'A'}).json()['id']
        self.body = {'question': '查演示药', 'session_id': self.id, 'turn_id': 't1',
                     'workspace': {'store_id': 'A', 'task': 'drug_lookup', 'customer': {}}}

    def events(self, response):
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]

    def test_sse_done_is_saved_and_restored_by_session_id(self):
        response = self.client.post('/api/agent/stream', json=self.body)
        self.assertEqual(self.events(response)[-1]['event'], 'done')
        saved = self.client.get('/api/chats/' + self.id, params={'store_id': 'A'}).json()
        self.assertEqual(saved['title'], self.body['question'])
        self.assertTrue(saved['turns'][0]['completed'])
        cards = saved['turns'][0]['cards']
        self.assertTrue(any(card['type'] == 'drug_candidate' for card in cards))
        self.assertEqual(self.client.post('/api/agent/stream', json=self.body).status_code, 409)

    def test_next_turn_uses_saved_history_not_client_forged_history(self):
        self.client.post('/api/agent/stream', json=self.body)
        response = self.client.post('/api/agent/stream', json={**self.body, 'turn_id': 't2',
            'question': '再查演示药', 'history': [{'role': 'user', 'content': 'FORGED_HISTORY'}]})
        self.assertEqual(self.events(response)[-1]['event'], 'done')
        history = json.dumps(self.requests[-1]['history'], ensure_ascii=False)
        self.assertIn('查演示药', history)
        self.assertNotIn('FORGED_HISTORY', history)

    def test_foreign_owner_cannot_load_or_append_and_client_cannot_choose_owner(self):
        self.assertEqual(self.client.post('/api/chats', json={'store_id': 'A', 'owner_id': 'u2'}).status_code, 422)
        self.owner = 'u2'
        self.assertEqual(self.client.get('/api/chats', params={'store_id': 'A'}).json()['sessions'], [])
        self.assertEqual(self.client.get('/api/chats/' + self.id, params={'store_id': 'A'}).status_code, 404)
        self.assertEqual(self.client.post('/api/agent/stream', json=self.body).status_code, 404)

    def test_save_failure_is_error_not_a_false_done(self):
        with patch.object(self.store, 'finish_turn', side_effect=sqlite3.OperationalError('test')):
            events = self.events(self.client.post('/api/agent/stream', json=self.body))
        self.assertEqual(events[-1]['event'], 'error')
        self.assertEqual(events[-1]['payload']['code'], 'CHAT_SAVE_FAILED')
        saved = self.store.get(self.id, 'u1', 'A')['turns'][0]
        self.assertFalse(saved['completed'])
        self.assertEqual(saved['cards'], [])

    def test_emergency_result_is_persisted_without_inventory_access(self):
        with patch('meditouch.api.inventory_is_mock', side_effect=AssertionError('No inventory')):
            events = self.events(self.client.post('/api/agent/stream', json={**self.body, 'question': '顾客现在呼吸困难'}))
        self.assertEqual(events[-1]['event'], 'done')
        saved = self.store.get(self.id, 'u1', 'A')['turns'][0]
        self.assertTrue(saved['completed'])
        self.assertEqual(saved['cards'][0]['type'], 'intercept_warning')
