import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meditouch.medical_tools import EvidenceTools
from meditouch.server import SEEDS


class EvidenceToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'evidence.db'
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE docs(id TEXT PRIMARY KEY, body TEXT)')
            c.executemany('INSERT INTO docs VALUES (?, ?)',
                          [(d['id'], json.dumps(d, ensure_ascii=False)) for d in SEEDS])
        c.close()
        self.tools = EvidenceTools(self.db)

    def test_conflict_snapshot_is_read_only_and_never_calls_model(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        with patch('meditouch.server.generate', side_effect=AssertionError('unexpected model call')):
            result = self.tools.analyze_evidence('演示措施X')
        self.assertEqual(result['status'], 'unresolved')
        group = result['trace'][1]['result']['groups'][0]
        self.assertEqual(group['retired'], ['demo-old'])
        self.assertEqual({a['source_id'] for a in group['claims']}, {'demo-new', 'demo-other'})
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_missing_database_is_not_created(self):
        missing = self.db.parent / 'missing.db'
        with self.assertRaises(ValueError):
            EvidenceTools(missing).search_evidence('演示措施X')
        self.assertFalse(missing.exists())

    def test_invalid_questions_and_no_evidence(self):
        for q in ['', ' ', 12, 'x' * 20001]:
            with self.assertRaises(ValueError): self.tools.analyze_evidence(q)
        self.assertEqual(self.tools.analyze_evidence('zzzzunknown')['status'], 'insufficient')

    def test_sources_are_data_not_paths(self):
        self.assertEqual(self.tools.get_evidence_source('demo-new')['source']['id'], 'demo-new')
        with self.assertRaises(ValueError): self.tools.get_evidence_source('../server.py')


if __name__ == '__main__':
    unittest.main()
