"""Short-lived SQLite snapshots of successful public evidence lookups."""
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / 'data' / 'public-evidence-cache.db'
TTL_SECONDS = 12 * 3600
MAX_ENTRIES = 256
SCHEMA = '''CREATE TABLE IF NOT EXISTS snapshots (
    key TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at REAL NOT NULL)'''


def cache_path():
    return Path(os.getenv('MED_AGENT_PUBLIC_CACHE_DB') or DEFAULT_DB)


def read_snapshot(key, trace):
    path = cache_path()
    if not path.is_file():
        return None
    try:
        with closing(sqlite3.connect(path, timeout=0.2)) as db:
            row = db.execute('SELECT payload, fetched_at FROM snapshots WHERE key=? AND fetched_at>?',
                             (json.dumps(key), time.time() - TTL_SECONDS)).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        trace.append({'node': 'public_cache', 'status': 'hit', 'source': key[0],
                      'fetched_at': datetime.fromtimestamp(row[1], timezone.utc).isoformat()})
        return payload
    except (OSError, sqlite3.Error, ValueError):
        return None


def write_snapshot(key, payload):
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=0.2)) as db, db:
            db.execute(SCHEMA)
            now = time.time()
            db.execute('INSERT INTO snapshots VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET '
                       'payload=excluded.payload, fetched_at=excluded.fetched_at',
                       (json.dumps(key), json.dumps(payload, ensure_ascii=False), now))
            db.execute('DELETE FROM snapshots WHERE fetched_at<=?', (now - TTL_SECONDS,))
            db.execute('DELETE FROM snapshots WHERE key IN (SELECT key FROM snapshots '
                       'ORDER BY fetched_at DESC, key LIMIT -1 OFFSET ?)', (MAX_ENTRIES,))
    except (OSError, sqlite3.Error):
        # Cache failure must leave the live provider response usable.
        pass
