"""Health explanations, real graph routing and source degradation on synthetic evidence."""
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from meditouch.health_knowledge import medline_search, lookup_health_evidence, nhs_content
from meditouch.pharmacy_health import generate_health_answer
from meditouch.pharmacy_graph import build_pharmacy_graph, prepare_inventory_state
from meditouch.pharmacy_ui import graph_ui_events
from meditouch.inventory_models import create_inventory_engine, create_inventory_tables, DrugInventory
from sqlmodel import Session
from fastapi.testclient import TestClient
from meditouch.api import create_app
from meditouch.chat_storage import ChatStore

SOURCE = {'id': 'test-fever', 'title': 'Fever', 'publisher': 'Synthetic test source',
          'url': 'https://medlineplus.gov/fever.html', 'text': 'Measure temperature. Seek medical help for breathing difficulty.', 'kind': 'public'}


def answer(*, text='先测量体温，确认是否发热。', quote='Measure temperature.', citation=True):
    return {'sections': [{'title': '先做什么', 'items': [{'text': text, 'highlights': ['测量体温'],
        'citations': [{'source_id': SOURCE['id'], 'quote': quote}] if citation else []}]}],
        'follow_up': {'question': '是否已经测过体温？', 'options': ['尚未测量', '已经测量']}}


class Model:
    def __init__(self, *, both=False, reject=False, malformed=False):
        self.calls = []
        self.both, self.reject, self.malformed = both, reject, malformed

    def complete(self, messages):
        value = json.loads(messages[1]['content']); self.calls.append(value)
        if 'query' in value:
            result = {'intent': 'health_qa', 'symptoms': ['发热'], 'medical_topics': [],
                      'target_drugs': ['演示药'] if self.both else [], 'customer_tags': [],
                      'needs_inventory': self.both,
                      'knowledge_queries': [{'en': 'Fever', 'zh': '发热'}]}
        elif 'claims' in value:
            result = {'supported': [not self.reject] * len(value['claims'])}
        elif 'approved_scripts' in value:
            result = {'script_id': next(iter(value['approved_scripts']))}
        else:
            result = answer(citation=value['source_mode'] == 'evidence')
        return {'role': 'assistant', 'content': 'not json' if self.malformed else json.dumps(result, ensure_ascii=False)}


class PharmacyHealthTests(unittest.TestCase):
    def state(self):
        return {'user_query': '病人发烧怎么办', 'symptoms': ['发热'], 'customer_tags': [],
                'model_calls': 0, 'tool_calls': 0, 'trace': []}

    def loader(self, state, **kwargs):
        return [dict(SOURCE)], []

    def test_general_health_question_answers_without_inventory_or_safety_tools(self):
        model = Model()
        initial = prepare_inventory_state('病人发烧怎么办', {'store_id': 'A', 'task': 'drug_lookup', 'customer': {}}, model=model)
        def forbidden(*args, **kwargs):
            self.fail('Health-only questions must not query inventory/safety')
        graph = build_pharmacy_graph(None, model=model, tool_factory=forbidden, health_evidence_loader=self.loader)
        result = graph.invoke(initial)
        self.assertEqual(result['outcome'], 'health_answer')
        self.assertEqual(result['inventory_attempts'], 0)
        self.assertEqual([card['type'] for card in result['ui_cards']], ['health_answer'])
        self.assertIn('测量体温', result['answer'])
        self.assertEqual(result['health_card']['basis'], 'public')
        events = list(graph_ui_events(graph, initial, 'health-test'))
        self.assertEqual(events[-1]['event'], 'done')
        cards = [event['payload']['cards'] for event in events if event['event'] == 'cards.snapshot']
        self.assertTrue(all(not any(card['type'] == 'drug_candidate' for card in batch) for batch in cards))

    def test_health_and_inventory_keep_both_results_and_unknown(self):
        engine = create_inventory_engine('sqlite://'); self.addCleanup(engine.dispose)
        create_inventory_tables(engine)
        with Session(engine) as session:
            session.add(DrugInventory(name='演示药', shelf_location='A2', stock=4)); session.commit()
        model = Model(both=True)
        initial = prepare_inventory_state('发热，演示药本店有吗', {'store_id': 'A', 'task': 'drug_lookup', 'customer': {}}, model=model)
        result = build_pharmacy_graph(engine, model=model, health_evidence_loader=self.loader).invoke(initial)
        self.assertEqual(result['safety_status'], 'unknown')
        self.assertEqual(result['ui_cards'][0]['type'], 'health_answer')
        self.assertTrue(any(card['type'] == 'drug_candidate' for card in result['ui_cards']))
        self.assertIn('测量体温', result['answer'])
        self.assertEqual(result['inventory_results'][0]['stock'], 4)

    def test_emergency_bypasses_health_retrieval_as_well_as_inventory(self):
        initial = prepare_inventory_state('现在胸痛呼吸困难', {'store_id': 'A', 'task': 'drug_lookup', 'customer': {}}, model=Model())
        def forbidden(*args, **kwargs):
            self.fail('Emergency must stop before health knowledge lookup')
        result = build_pharmacy_graph(None, health_evidence_loader=forbidden).invoke(initial)
        self.assertEqual(result['outcome'], 'risk_alert')

    def test_unknown_does_not_block_general_explanation(self):
        result = generate_health_answer({**self.state(), 'safety_status': 'unknown'}, model=Model(), evidence_loader=self.loader)
        self.assertEqual(result['health_status'], 'answered')
        self.assertEqual(result['health_card']['sections'][0]['items'][0]['source_ids'], [SOURCE['id']])

    def test_sse_health_without_stock_and_chat_restore_preserve_answer(self):
        with tempfile.TemporaryDirectory() as folder, patch('meditouch.pharmacy_health.lookup_health_evidence', side_effect=self.loader), patch.dict(os.environ, {'MED_AGENT_INVENTORY_STORE_ID': 'A'}):
            chats = ChatStore(Path(folder)/'chats.db')
            with TestClient(create_app(engine_resolver=lambda store: None, model_factory=Model, chat_store=chats)) as client:
                created = client.post('/api/chats', json={'store_id': 'A'}).json()
                response = client.post('/api/agent/stream', json={'question': '病人发烧怎么办',
                    'session_id': created['id'], 'turn_id': 'health-turn',
                    'workspace': {'store_id': 'A', 'task': 'drug_lookup', 'customer': {}}})
                self.assertEqual(response.status_code, 200, response.text)
                frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                self.assertEqual(frames[-1]['event'], 'done')
                snapshot = next(frame for frame in frames if frame['event'] == 'cards.snapshot')
                self.assertEqual(snapshot['payload']['cards'][0]['type'], 'health_answer')
                saved = client.get('/api/chats/'+created['id'], params={'store_id': 'A'}).json()
                self.assertEqual(saved['turns'][0]['cards'][0]['type'], 'health_answer')

    def test_no_evidence_is_labeled_model_knowledge_without_citations(self):
        result = generate_health_answer(self.state(), model=Model(), evidence_loader=lambda *a, **kw: ([], []))
        self.assertEqual(result['health_card']['basis'], 'model_knowledge')
        self.assertIn('通用医学知识', result['health_card']['disclaimer'])
        self.assertEqual(result['health_sources'], [])

    def test_rejected_entailment_degrades_to_explicit_knowledge_mode(self):
        result = generate_health_answer(self.state(), model=Model(reject=True), evidence_loader=self.loader)
        self.assertEqual(result['health_card']['basis'], 'model_knowledge')
        self.assertEqual(result['health_sources'], [])

    def test_forged_quote_and_inventory_promises_are_never_displayed(self):
        class Bad(Model):
            def complete(self, messages):
                value = json.loads(messages[1]['content'])
                result = answer(quote='This quote is not in the source') if value.get('source_mode') == 'evidence' else answer(text='本店有货，放心吃', citation=False)
                return {'role': 'assistant', 'content': json.dumps(result)}
        result = generate_health_answer(self.state(), model=Bad(), evidence_loader=self.loader)
        self.assertEqual(result['health_status'], 'unavailable')
        self.assertNotIn('放心吃', result['health_answer'])
        self.assertEqual(result['health_sources'], [])

    def test_model_errors_do_not_leak_credentials_or_disguise_as_answer(self):
        class Broken:
            def complete(self, *args):
                raise RuntimeError('private-token-from-provider')
        result = generate_health_answer(self.state(), model=Broken(), evidence_loader=self.loader)
        self.assertEqual(result['health_status'], 'unavailable')
        self.assertNotIn('private-token', str(result))

    def test_public_lookup_caches_and_offline_local_fallback_work(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'MED_AGENT_PUBLIC_CACHE_DB': str(Path(folder)/'cache.db'),
                'MED_AGENT_NHS_API_KEY': '', 'MED_AGENT_SHOWAPI_APP_KEY': '', 'MED_AGENT_PUBLIC_KNOWLEDGE': '1'}):
            state = {**self.state(), 'knowledge_queries': [{'en': 'Fever', 'zh': '发热'}]}
            with patch('meditouch.health_knowledge.medline_search', return_value=[dict(SOURCE)]) as remote:
                first, _ = lookup_health_evidence(state)
                second, trace = lookup_health_evidence(state)
                self.assertEqual(first, second); self.assertEqual(remote.call_count, 1)
                self.assertTrue(any(event.get('status') == 'hit' for event in trace))
            class Local:
                def search(self, *args, **kwargs):
                    return {'items': [{'id': 'local-fever', 'text': 'local evidence',
                        'source': {'title': '本地发热资料', 'url': 'https://medlineplus.gov/fever.html'}}]}
            state['knowledge_queries'][0]['en'] = 'Other topic'
            with patch('meditouch.health_knowledge.medline_search', side_effect=OSError()):
                found, _ = lookup_health_evidence(state, local_service=Local())
                self.assertEqual(found[0]['kind'], 'local')

    def test_medline_xml_is_parsed_and_untrusted_urls_are_rejected(self):
        xml = b'<nlmSearchResult><health-topic title="Fever" url="https://medlineplus.gov/fever.html"><full-summary>&lt;p&gt;Measure temperature.&lt;/p&gt;</full-summary></health-topic><health-topic title="bad" url="http://127.0.0.1/private"><full-summary>bad</full-summary></health-topic></nlmSearchResult>'
        with patch('meditouch.health_knowledge.PROVIDER_HTTP.open', return_value=BytesIO(xml)):
            result = medline_search('Fever')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['text'], 'Measure temperature.')

    def test_nhs_dynamic_path_has_fixed_origin_and_preserves_attribution(self):
        with patch('meditouch.health_knowledge.fetch_json', return_value={'name': 'Fever',
                'hasPart': [{'hasPart': [{'text': '<p>Synthetic NHS fixture.</p>'}]}]}) as fetch:
            rows = nhs_content('symptoms/fever-in-adults', 'fake-test-key')
            self.assertEqual(rows[0]['text'], 'Synthetic NHS fixture.')
            self.assertIn('attribution_logo', rows[0])
            self.assertTrue(fetch.call_args.args[0].startswith('https://api.service.nhs.uk/'))
        with patch('meditouch.health_knowledge.fetch_json') as fetch:
            self.assertEqual(nhs_content('https://untrusted.example/steal', 'fake-test-key'), [])
            fetch.assert_not_called()

    def test_invalid_optional_nhs_hint_does_not_destroy_semantic_intake(self):
        from meditouch.pharmacy_semantics import KnowledgeQuery
        query = KnowledgeQuery(en='Fever', zh='发热', nhs_path='../../private')
        self.assertEqual(query.nhs_path, '')

    def test_exact_medline_topic_wins_over_broader_search_matches(self):
        xml = b'<nlmSearchResult><health-topic title="Fever" url="https://medlineplus.gov/fever.html"><full-summary>Fever fixture</full-summary></health-topic><health-topic title="Hemorrhagic Fevers" url="https://medlineplus.gov/hemorrhagicfevers.html"><full-summary>Other fever fixture</full-summary></health-topic></nlmSearchResult>'
        with patch('meditouch.health_knowledge.PROVIDER_HTTP.open', return_value=BytesIO(xml)):
            self.assertEqual([source['title'] for source in medline_search('Fever')], ['Fever'])


if __name__ == '__main__':
    unittest.main()
