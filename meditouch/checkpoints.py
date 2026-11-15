"""Server-scoped, per-turn durable LangGraph checkpoints for the single-process demo."""
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver


def thread_config(owner, store, session_id, turn_id):
    identity = json.dumps([owner, store, session_id, turn_id], ensure_ascii=False)
    return {'configurable': {'thread_id': sha256(identity.encode()).hexdigest()}}


@contextmanager
def checkpoint_saver(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10, check_same_thread=False)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        saver = SqliteSaver(connection)
        saver.setup()
        yield saver
    finally:
        connection.close()
