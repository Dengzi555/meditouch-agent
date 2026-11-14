"""Local settings persistence, redaction, startup reload and access boundaries."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from meditouch.api import create_app
from meditouch.settings import SettingsStore, load_local_env, read_env


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / '.env'
        self.env = {'MED_AGENT_API_KEY': 'fake-text-secret', 'MED_AGENT_VISION_API_KEY': 'fake-vision-secret',
                    'MED_AGENT_MODEL': 'fixture-model'}
        self.store = SettingsStore(self.path, self.env)
        self.client = TestClient(create_app(settings_store=self.store),
                                 base_url='http://127.0.0.1', client=('127.0.0.1', 55000))
        self.addCleanup(self.client.close)

    def test_read_redacts_secrets_and_does_not_write(self):
        response = self.client.get('/api/settings')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('fake-text-secret', response.text)
        self.assertNotIn('fake-vision-secret', response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertFalse(self.path.exists())

    def test_save_preserves_unrelated_settings_and_unedited_keys(self):
        self.path.write_text('# local settings\nPORT=8765\nOTHER_VALUE="keep me"\n', encoding='utf-8')
        response = self.client.post('/api/settings', json={'values': {
            'MED_AGENT_BASE_URL': 'https://example.com/v1', 'MED_AGENT_MODEL': 'new-model'}})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['saved'])
        values, _ = read_env(self.path)
        self.assertEqual(values['OTHER_VALUE'], 'keep me')
        self.assertEqual(values['PORT'], '8765')
        self.assertEqual(values['MED_AGENT_API_KEY'], 'fake-text-secret')
        self.assertEqual(self.env['MED_AGENT_MODEL'], 'new-model')
        self.assertNotIn('fake-text-secret', response.text)

    def test_secret_roundtrip_clear_and_restart_precedence(self):
        secret = 'test-$not_expanded-#-"-\\-value'
        result = self.client.post('/api/settings', json={'values': {'MED_AGENT_API_KEY': secret}})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(read_env(self.path)[0]['MED_AGENT_API_KEY'], secret)
        self.assertNotIn(secret, result.text)
        self.client.post('/api/settings', json={'values': {'MED_AGENT_API_KEY': ''}})
        with patch.dict('os.environ', {'MED_AGENT_API_KEY': 'old-launcher-key', 'PORT': '9000'}, clear=True):
            load_local_env(self.path)
            import os
            self.assertEqual(os.environ['MED_AGENT_API_KEY'], '')
            self.assertEqual(os.environ['PORT'], '9000')

    def test_rejects_unknown_keys_newlines_invalid_addresses_without_echo(self):
        for values in ({'PATH': 'bad'}, {'MED_AGENT_API_KEY': 'sensitive\nINJECTED=1'},
                       {'MED_AGENT_BASE_URL': 'javascript:bad'}, {'MED_AGENT_MODEL': 123},
                       {'MED_AGENT_BASE_URL': 'https://host/v1?api_key=sensitive'},
                       {'MED_AGENT_SHOWAPI_DETAIL': 'maybe'}):
            with self.subTest(values=list(values)):
                response = self.client.post('/api/settings', json={'values': values})
                self.assertEqual(response.status_code, 422)
                self.assertNotIn('sensitive', response.text)
                self.assertFalse(self.path.exists())

    def test_denies_remote_host_origin_and_proxy_context(self):
        for headers in ({'Host': 'public.example'}, {'Origin': 'https://evil.example'},
                        {'CF-Connecting-IP': '8.8.8.8'}, {'X-Forwarded-Host': 'public.example'},
                        {'Sec-Fetch-Site': 'cross-site'}):
            for method in ('get', 'post'):
                with self.subTest(headers=headers, method=method):
                    response = getattr(self.client, method)('/api/settings', headers=headers)
                    self.assertEqual(response.status_code, 403)
        remote = TestClient(create_app(settings_store=self.store), base_url='http://127.0.0.1',
                            client=('192.168.1.2', 55000))
        with remote:
            self.assertEqual(remote.get('/api/settings').status_code, 403)
        self.assertFalse(self.path.exists())

    def test_atomic_failure_does_not_update_environment_or_file(self):
        self.path.write_text('OTHER=keep\n', encoding='utf-8')
        with patch('meditouch.settings.os.replace', side_effect=OSError('private-path')):
            response = self.client.post('/api/settings', json={'values': {'MED_AGENT_MODEL': 'changed'}})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private-path', response.text)
        self.assertEqual(self.env['MED_AGENT_MODEL'], 'fixture-model')
        self.assertEqual(self.path.read_text(), 'OTHER=keep\n')
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_repeated_save_has_single_assignment_and_developer_origin_allowed(self):
        for name in ('one', 'two'):
            response = self.client.post('/api/settings', json={'values': {'MED_AGENT_MODEL': name}},
                                        headers={'Origin': 'http://127.0.0.1:5173'})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.path.read_text().count('MED_AGENT_MODEL='), 1)
        self.assertEqual(read_env(self.path)[0]['MED_AGENT_MODEL'], 'two')

    def test_malformed_json_never_echoes_body(self):
        response = self.client.post('/api/settings', content='{"fake-secret":',
                                    headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('fake-secret', response.text)
