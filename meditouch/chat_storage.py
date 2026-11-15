"""Local chat persistence, scoped by server-owned user identity and store ID."""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
from threading import RLock
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
STALE_SECONDS = 300
PROCESS_TOKEN = uuid4().hex  # Single-process demo: distinguishes a restarted server from a live request.


class ChatNotFound(Exception):
    pass


class ChatBusy(Exception):
    pass


class ChatStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        self._ready = False
        self._lock = RLock()

    def _connect(self):
        with self._lock:
            if self.path is None:
                self.path = Path(os.getenv('MED_AGENT_CHAT_DB', str(ROOT / 'data/pharmacy_chat.db')))
            if not self._ready:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(self.path, timeout=5)) as connection:
                    connection.execute('PRAGMA journal_mode=WAL')
                    connection.executescript('''
                        CREATE TABLE IF NOT EXISTS pharmacy_chat_sessions (
                            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, store_id TEXT NOT NULL,
                            title TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_pharmacy_chat_owner
                            ON pharmacy_chat_sessions(owner_id, store_id, updated_at);
                        CREATE TABLE IF NOT EXISTS pharmacy_chat_messages (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            session_id TEXT NOT NULL REFERENCES pharmacy_chat_sessions(id),
                            turn_id TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                            content TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL DEFAULT '{}',
                            status TEXT NOT NULL CHECK(status IN ('completed','processing','interrupted','failed')),
                            created_at REAL NOT NULL, updated_at REAL NOT NULL,
                            UNIQUE(session_id, turn_id, role)
                        );
                        CREATE INDEX IF NOT EXISTS idx_pharmacy_chat_message
                            ON pharmacy_chat_messages(session_id, id);
                    ''')
                    columns = {row[1] for row in connection.execute('PRAGMA table_info(pharmacy_chat_messages)')}
                    if 'executor_token' not in columns:
                        connection.execute("ALTER TABLE pharmacy_chat_messages ADD COLUMN executor_token TEXT NOT NULL DEFAULT ''")
                self._ready = True
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        return connection

    def _session(self, db, session_id, owner_id, store_id):
        row = db.execute('SELECT * FROM pharmacy_chat_sessions WHERE id=? AND owner_id=? AND store_id=?',
                         (session_id, owner_id, store_id)).fetchone()
        if row is None:
            raise ChatNotFound()
        return row

    def _expire(self, db, session_id):
        db.execute("UPDATE pharmacy_chat_messages SET status='interrupted',updated_at=? "
                   "WHERE session_id=? AND status='processing' AND (updated_at<? OR executor_token<>?)",
                   (time.time(), session_id, time.time() - STALE_SECONDS, PROCESS_TOKEN))

    def create(self, owner_id, store_id):
        session_id, now = uuid4().hex, time.time()
        with closing(self._connect()) as db:
            db.execute('INSERT INTO pharmacy_chat_sessions VALUES (?,?,?,?,?,?)',
                       (session_id, owner_id, store_id, '新咨询', now, now))
        return {'id': session_id, 'title': '新咨询', 'created_at': now, 'updated_at': now, 'turns': []}

    def list_sessions(self, owner_id, store_id):
        with closing(self._connect()) as db:
            rows = db.execute('SELECT id,title,created_at,updated_at FROM pharmacy_chat_sessions '
                'WHERE owner_id=? AND store_id=? ORDER BY updated_at DESC LIMIT 200', (owner_id, store_id)).fetchall()
        return [dict(row) for row in rows]

    def get(self, session_id, owner_id, store_id):
        with closing(self._connect()) as db:
            session = dict(self._session(db, session_id, owner_id, store_id))
            self._expire(db, session_id)
            messages = db.execute('SELECT * FROM pharmacy_chat_messages WHERE session_id=? ORDER BY id',
                                  (session_id,)).fetchall()
        turns, customer = {}, {}
        for row in messages:
            data = json.loads(row['payload'])
            if row['role'] == 'user':
                turns[row['turn_id']] = {'id': row['turn_id'], 'question': row['content'], 'cards': [],
                    'overall': 'unknown', 'stages': [], 'error': '', 'completed': False, 'loading': False}
                customer = data.get('customer', {})
            elif row['turn_id'] in turns:
                completed = row['status'] == 'completed'
                turns[row['turn_id']].update(cards=data.get('cards', []) if completed else [],
                    overall=data.get('overall', 'unknown') if completed else 'unknown',
                    stages=data.get('stages', []), completed=completed, status=row['status'],
                    error='' if completed else '上次请求尚在处理，请稍后重新打开此会话' if row['status'] == 'processing'
                    else '上次请求未完成，可继续处理；库存与禁忌将重新核查')
        return {key: session[key] for key in ('id', 'title', 'created_at', 'updated_at')} | {
            'turns': list(turns.values()), 'customer': customer}

    def _history(self, db, session_id):
        rows = db.execute('SELECT * FROM (SELECT * FROM pharmacy_chat_messages WHERE session_id=? '
                          'ORDER BY id DESC LIMIT 20) ORDER BY id', (session_id,)).fetchall()
        history = []
        for row in rows:
            data = json.loads(row['payload'])
            if row['role'] == 'user':
                text = row['content']
                if data.get('customer'):
                    text = '店员当时确认的信息：' + json.dumps(data['customer'], ensure_ascii=False) + '\n' + text
                history.append({'role': 'user', 'content': text[:2000]})
            elif row['status'] == 'completed':
                parts = []
                business = data.get('business_state') or {}
                if business.get('medical_topics'):
                    parts.append('此前咨询主题（非确诊）：' + '、'.join(business['medical_topics']))
                if business.get('standalone_query'):
                    parts.append('此前完整问题：' + business['standalone_query'])
                names = [card['drug']['name'] for card in data.get('cards', []) if card.get('type') == 'drug_candidate']
                if names:
                    parts.append('此前库存候选（不是顾客已用药或安全批准）：' + '、'.join(names))
                health = next((card for card in data.get('cards', []) if card.get('type') == 'health_answer'), None)
                if health:
                    parts.append('此前回答主题（不代表顾客病情或已用药）：' + '；'.join(
                        section.get('title', '') + '：' + ' '.join(point.get('text', '') for point in section.get('items', []))
                        for section in health.get('sections', [])))
                emergency = next((card for card in data.get('cards', []) if card.get('type') == 'emergency_support'), None)
                if emergency:
                    parts.append('此前店员确认的现场观察（非诊断；未知不等于正常）：' +
                                 json.dumps(emergency['facts'], ensure_ascii=False))
                if parts:
                    history.append({'role': 'assistant', 'content': '\n'.join(parts)[:2000]})
        return history[-20:]

    def begin_turn(self, session_id, owner_id, store_id, turn_id, question, customer):
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            self._session(db, session_id, owner_id, store_id)
            self._expire(db, session_id)
            if db.execute('SELECT 1 FROM pharmacy_chat_messages WHERE session_id=? AND '
                          "(turn_id=? OR status='processing') LIMIT 1", (session_id, turn_id)).fetchone():
                raise ChatBusy()
            history = self._history(db, session_id)
            first = not db.execute('SELECT 1 FROM pharmacy_chat_messages WHERE session_id=? LIMIT 1',
                                   (session_id,)).fetchone()
            now = time.time()
            for role, content, payload, status in (
                ('user', question, {'customer': customer}, 'completed'),
                ('assistant', '', {}, 'processing'),
            ):
                db.execute('INSERT INTO pharmacy_chat_messages '
                           '(session_id,turn_id,role,content,payload,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                           (session_id, turn_id, role, content, json.dumps(payload, ensure_ascii=False), status, now, now))
            db.execute('UPDATE pharmacy_chat_sessions SET updated_at=?, title=CASE WHEN ? THEN ? ELSE title END WHERE id=?',
                       (now, first, question[:80], session_id))
            db.execute('UPDATE pharmacy_chat_messages SET executor_token=? WHERE session_id=? AND turn_id=?',
                       (PROCESS_TOKEN, session_id, turn_id))
            db.commit()
            return history
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def resume_turn(self, session_id, owner_id, store_id, turn_id):
        """Atomically claim only the latest incomplete turn; never create another user message."""
        with closing(self._connect()) as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                self._session(db, session_id, owner_id, store_id)
                self._expire(db, session_id)
                row = db.execute("SELECT * FROM pharmacy_chat_messages WHERE session_id=? AND turn_id=? AND role='assistant'",
                                 (session_id, turn_id)).fetchone()
                latest = db.execute("SELECT turn_id FROM pharmacy_chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 1",
                                    (session_id,)).fetchone()
                busy = db.execute("SELECT 1 FROM pharmacy_chat_messages WHERE session_id=? AND status='processing'",
                                  (session_id,)).fetchone()
                if row is None:
                    raise ChatNotFound()
                payload = json.loads(row['payload'])
                attempts = payload.get('resume_count', 0)
                if busy or row['status'] == 'completed' or latest['turn_id'] != turn_id or attempts >= 3:
                    raise ChatBusy()
                user = db.execute("SELECT content,payload FROM pharmacy_chat_messages WHERE session_id=? AND turn_id=? AND role='user'",
                                  (session_id, turn_id)).fetchone()
                payload['resume_count'] = attempts + 1
                db.execute("UPDATE pharmacy_chat_messages SET status='processing',payload=?,executor_token=?,updated_at=? WHERE id=?",
                           (json.dumps(payload, ensure_ascii=False), PROCESS_TOKEN, time.time(), row['id']))
                history = self._history(db, session_id)
                db.commit()
                return {'question': user['content'], 'customer': json.loads(user['payload']).get('customer', {}),
                        'history': history}
            except Exception:
                db.rollback()
                raise

    def finish_turn(self, session_id, owner_id, store_id, turn_id, state, stages):
        fields = ('intent', 'symptoms', 'denied_symptoms', 'target_drugs', 'customer_tags',
                  'excluded_drugs', 'excluded_reasons', 'safety_status', 'safety_reason',
                  'medical_topics', 'standalone_query', 'context_resolution', 'knowledge_queries',
                  'needs_inventory', 'needs_health_answer')
        payload = {'cards': state.get('ui_cards', []), 'overall': state.get('safety_status', 'unknown'),
                   'stages': stages, 'business_state': {key: state[key] for key in fields if key in state}}
        with closing(self._connect()) as db:
            self._session(db, session_id, owner_id, store_id)
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute("UPDATE pharmacy_chat_messages SET content=?,payload=?,status='completed',updated_at=? "
                "WHERE session_id=? AND turn_id=? AND role='assistant' AND status='processing'",
                (state.get('answer', ''), json.dumps(payload, ensure_ascii=False), time.time(), session_id, turn_id)).rowcount
            if changed != 1:
                db.rollback()
                raise ChatBusy()
            db.execute('UPDATE pharmacy_chat_sessions SET updated_at=? WHERE id=?', (time.time(), session_id))
            db.commit()

    def interrupt_turn(self, session_id, owner_id, store_id, turn_id, *, failed=False):
        with closing(self._connect()) as db:
            self._session(db, session_id, owner_id, store_id)
            db.execute("UPDATE pharmacy_chat_messages SET status=?,updated_at=? WHERE session_id=? "
                       "AND turn_id=? AND role='assistant' AND status='processing'",
                       ('failed' if failed else 'interrupted', time.time(), session_id, turn_id))

    def update_emergency(self, session_id, owner_id, store_id, turn_id, revision, changes):
        """Patch observations on the latest completed emergency turn, without invoking any graph/tools."""
        from meditouch.emergency_support import build_emergency_card
        with closing(self._connect()) as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                self._session(db, session_id, owner_id, store_id)
                row = db.execute("SELECT * FROM pharmacy_chat_messages WHERE session_id=? AND turn_id=? AND role='assistant'",
                                 (session_id, turn_id)).fetchone()
                if row is None:
                    raise ChatNotFound()
                latest = db.execute('SELECT turn_id FROM pharmacy_chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 1',
                                    (session_id,)).fetchone()
                payload = json.loads(row['payload'])
                card = next((c for c in payload.get('cards', []) if c.get('type') == 'emergency_support'), None)
                if row['status'] != 'completed' or latest['turn_id'] != turn_id or card is None or card['revision'] != revision:
                    raise ChatBusy()
                updated = build_emergency_card(card['original_query'], symptoms=card['symptoms'], previous=card, changes=changes)
                payload['cards'] = [updated if c.get('id') == card['id'] else c for c in payload['cards']]
                db.execute('UPDATE pharmacy_chat_messages SET content=?,payload=?,updated_at=? WHERE id=?',
                           ('\n'.join(updated['actions']) + '\n\n' + updated['handover'],
                            json.dumps(payload, ensure_ascii=False), time.time(), row['id']))
                db.execute('UPDATE pharmacy_chat_sessions SET updated_at=? WHERE id=?', (time.time(), session_id))
                db.commit()
                return updated
            except Exception:
                db.rollback()
                raise
