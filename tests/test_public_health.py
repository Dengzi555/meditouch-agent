import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meditouch.consultation import ConsultationStore, run_consultation
from meditouch.public_health import public_fallback


class PublicKnowledgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cache = patch.dict('os.environ', {'MED_AGENT_PUBLIC_CACHE_DB': str(Path(temp.name) / 'cache.db')})
        cache.start()
        self.addCleanup(cache.stop)

    @patch('meditouch.public_health.fetch_topic')
    def test_fresh_disk_snapshot_survives_provider_failure_but_expired_one_does_not(self, fetch):
        import sqlite3
        from contextlib import closing
        from meditouch.public_evidence_cache import cache_path

        question = '感冒有什么症状？'
        local = {'status': 'insufficient', 'trace': []}
        fetch.return_value = ('https://medlineplus.gov/commoncold.html',
                              ('Symptoms include: sneezing and coughing.',))
        first = public_fallback(local, question, {}, 'education', None)
        fetch.reset_mock()
        fetch.side_effect = TimeoutError()
        cached = public_fallback(local, question, {}, 'education', None)
        fetch.assert_not_called()
        self.assertEqual(cached['answer'], first['answer'])
        self.assertEqual(cached['trace'][0]['node'], 'public_cache')

        with closing(sqlite3.connect(cache_path())) as db, db:
            db.execute('UPDATE snapshots SET fetched_at=0')
        with patch('meditouch.public_health.time.sleep'):
            expired = public_fallback(local, question, {}, 'education', None)
        fetch.assert_called()
        self.assertEqual(expired['status'], 'insufficient')

    @patch.dict('os.environ', {'MED_AGENT_NHS_API_KEY': 'fixture', 'MED_AGENT_SHOWAPI_APP_KEY': ''})
    @patch('meditouch.public_health_providers.fetch_json')
    @patch('meditouch.public_health.fetch_with_retry')
    def test_nhs_fills_only_the_missing_intent(self, medline, fetch):
        fetch.return_value = {'name': 'Common cold', 'hasPart': [
            {'hasHealthAspect': 'https://schema.org/SymptomsHealthAspect',
             'hasPart': [{'@type': 'WebPageElement', 'text': '<ul><li>Fixture symptom</li></ul>'}]},
            {'hasHealthAspect': 'https://schema.org/SelfCareHealthAspect',
             'hasPart': [{'@type': 'WebPageElement', 'text': '<p>Provider care</p>'}]},
        ]}
        care = {'intent': 'care', 'title': '日常护理', 'has_evidence': True, 'excerpts': []}
        result = public_fallback({'status': 'answered', 'answer': 'Local care', 'answer_sections': [care]},
                                 '感冒有什么症状，怎么护理？', {}, 'education', None)
        self.assertIn('Fixture symptom', result['answer'])
        self.assertNotIn('Provider care', result['answer'])
        self.assertEqual(result['answer_sections'][0], care)
        self.assertEqual(result['sources'][0]['source_url'], 'https://www.nhs.uk/conditions/common-cold/')
        medline.assert_not_called()

    @patch.dict('os.environ', {'MED_AGENT_NHS_API_KEY': '', 'MED_AGENT_SHOWAPI_APP_KEY': 'fixture',
                              'MED_AGENT_SHOWAPI_DETAIL': '1'})
    @patch('meditouch.public_health_providers.showapi_body')
    def test_showapi_requires_exact_disease_before_detail(self, fetch):
        fetch.side_effect = [
            {'pagebean': {'contentlist': [
                {'name': '小儿感冒', 'id': 'child'}, {'name': '普通感冒', 'id': 'exact'},
            ]}},
            {'item': {'name': '普通感冒', 'id': 'exact', 'tagList': [
                {'name': '预防', 'content': 'Fixture prevention'},
                {'name': '用药', 'content': 'Unrequested medication'},
            ]}},
        ]
        result = public_fallback({'status': 'insufficient'}, '普通感冒怎么预防？', {}, 'education', None)
        self.assertEqual(fetch.call_args.args[2], {'id': 'exact'})
        self.assertEqual(result['answer'], 'Fixture prevention')
        self.assertEqual(result['answer_sections'][0]['intent'], 'prevention')

    @patch('meditouch.public_health.urlopen')
    def test_public_symptom_list_keeps_short_list_entries(self, urlopen):
        from html import escape
        from meditouch.public_health import fetch_topic

        summary = '<p>The symptoms of a common cold usually include:</p><ul><li>Sneezing</li><li>Coughing</li></ul>'
        urlopen.return_value.__enter__.return_value.read.return_value = (
            '<nlmSearchResult><health-topic title="Common Cold" url="https://medlineplus.gov/commoncold.html">'
            f'<full-summary>{escape(summary)}</full-summary></health-topic></nlmSearchResult>'
        ).encode()
        self.addCleanup(fetch_topic.cache_clear)
        fetch_topic.cache_clear()
        result = public_fallback({'status': 'insufficient', 'trace': []},
                                 '感冒有什么症状', {}, 'education', None)
        self.assertEqual(result['status'], 'guidance')
        self.assertIn('Sneezing; Coughing', result['answer'])

    @patch('meditouch.public_health.time.sleep')
    @patch('meditouch.public_health.fetch_topic')
    def test_transient_timeout_recovers_in_same_answer(self, fetch, pause):
        fetch.side_effect = [TimeoutError(), ('https://medlineplus.gov/hiv.html', (
            'HIV stands for human immunodeficiency virus.',
        ))]
        result = public_fallback({'status': 'insufficient', 'trace': []},
                                 'hiv你知道吗', {}, 'education', None)
        self.assertEqual(result['status'], 'guidance')
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result['trace'][0]['reason'], 'timeout')
        self.assertTrue(result['trace'][0]['retrying'])
        self.assertEqual(result['trace'][1]['attempt'], 2)
        pause.assert_called_once()

    @patch('meditouch.public_health.topic_model_reply')
    @patch('meditouch.public_health.fetch_topic')
    def test_unmapped_topic_requires_concept_match(self, fetch, model):
        fetch.return_value = ('https://medlineplus.gov/shingles.html', (
            'Shingles is an infection caused by the varicella-zoster virus.',
        ))
        local = {'status': 'insufficient', 'trace': []}
        model.side_effect = ['Shingles', 'YES', 'Shingles', 'NO']
        accepted = public_fallback(local, '带状疱疹你了解吗？', {}, 'education', None)
        rejected = public_fallback(local, '荨麻疹是什么？', {}, 'education', None)
        self.assertEqual(accepted['status'], 'guidance')
        self.assertEqual(accepted['trace'][-1]['match'], 'dynamic')
        self.assertEqual(rejected['status'], 'insufficient')
        self.assertNotIn('sources', rejected)

    @patch('meditouch.public_health.fetch_topic')
    def test_colloquial_hiv_question_uses_public_overview(self, fetch):
        fetch.return_value = ('https://medlineplus.gov/hiv.html', (
            'HIV stands for human immunodeficiency virus.',
        ))
        result = public_fallback({'status': 'insufficient', 'trace': []},
                                 'hiv你知道吗', {}, 'education', None)
        self.assertEqual(fetch.call_args.args[0], 'HIV')
        self.assertEqual(result['status'], 'guidance')
        self.assertEqual(result['answer_sections'][0]['intent'], 'overview')

    @patch('meditouch.public_health.fetch_topic')
    def test_public_lookup_ignores_denied_topic(self, fetch):
        fetch.return_value = ('', ())
        result = {'status': 'insufficient', 'trace': []}
        public_fallback(result, '头疼，没有发烧，咋回事？', {}, 'consultation', None)
        self.assertEqual(fetch.call_args.args[0], 'Headache')
        fetch.reset_mock()
        public_fallback(result, '没有头疼，不确定有没有发烧，咋回事？', {}, 'consultation', None)
        fetch.assert_not_called()

    headache_context = {'population': '成人', 'symptoms': '头疼', 'duration': '一天'}
    cough_context = {'population': '成人', 'symptoms': '咳嗽', 'duration': '一天'}
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cache = patch.dict('os.environ', {'MED_AGENT_PUBLIC_CACHE_DB': str(Path(temp.name) / 'cache.db')})
        cache.start()
        self.addCleanup(cache.stop)
        self.store = ConsultationStore(Path(temp.name) / 'consultations.db')
        self.store.initialize()

    def run_turn(self, **kwargs):
        def local(question, **options):
            return {'id': 'local', 'status': 'insufficient', 'question': question, 'trace': []}

        return run_consultation(self.store, runner=local, public_lookup=public_fallback, **kwargs)

    @patch('meditouch.public_health.fetch_topic')
    def test_api_first_skips_local_when_public_evidence_answers_question(self, fetch):
        fetch.return_value = ('https://medlineplus.gov/commoncold.html', (
            'Symptoms include: sneezing and coughing.',
        ))
        def local(*args, **kwargs):
            self.fail('complete public evidence should skip local retrieval')

        result = run_consultation(self.store, question='感冒有什么症状？',
                                  runner=local, public_lookup=public_fallback)
        self.assertEqual(result['intent_coverage'], {'symptoms': 'supported'})
        self.assertEqual(result['sources'][0]['source_url'], 'https://medlineplus.gov/commoncold.html')

    @patch.dict('os.environ', {'MED_AGENT_GENERAL_HEALTH_FALLBACK': '1'})
    @patch('meditouch.general_health.ModelGateway.complete')
    @patch('meditouch.web_evidence.web_search_fallback', side_effect=lambda result, *args, **kwargs: result)
    def test_general_health_runs_after_evidence_and_labels_unverified_answer(self, web, model):
        from meditouch.general_health import DISCLAIMER

        model.return_value = {'content': '{"answer":"这是一个医学术语的通用释义。"}'}

        def local(question, **options):
            web.assert_not_called()
            model.assert_not_called()
            return {'id': 'fixture', 'status': 'insufficient', 'sources': [{'id': 'unrelated'}]}

        result = run_consultation(self.store, question='什么是骨质疏松？', runner=local)
        web.assert_called_once()
        model.assert_called_once()
        self.assertEqual(result['status'], 'general_health')
        self.assertTrue(result['answer'].startswith(DISCLAIMER))
        self.assertEqual(result['sources'], [])

    @patch.dict('os.environ', {'MED_AGENT_GENERAL_HEALTH_FALLBACK': '1'})
    @patch('meditouch.general_health.ModelGateway.complete')
    def test_general_health_skips_medication_and_rejects_model_dosing(self, model):
        from meditouch.general_health import general_health_fallback
        from meditouch.query_plan import understand_question

        result = {'status': 'insufficient'}
        medication = '感冒药的用量是什么？'
        self.assertIs(general_health_fallback(result, medication, {}, 'education',
                                             plan=understand_question(medication, {})), result)
        model.assert_not_called()
        model.return_value = {'content': '{"answer":"建议服用20毫克。"}'}
        question = '什么是骨质疏松？'
        declined = general_health_fallback(result, question, {}, 'education',
                                           plan=understand_question(question, {}))
        self.assertEqual(declined['status'], 'insufficient')
        self.assertEqual(declined['trace'][-1]['status'], 'declined')

    @patch.dict('os.environ', {'MED_AGENT_DEEPSEEK_SEARCH_KEY': 'fixture'})
    @patch('meditouch.web_evidence.fetch_article')
    @patch('meditouch.web_evidence.fetch_json')
    def test_web_search_runs_after_local_and_uses_only_applicable_official_originals(self, search, article):
        url = 'https://www.chinacdc.cn/fixture'
        search.return_value = {'results': [
            {'url': 'https://www.chinacdc.cn.example.com/untrusted', 'snippet': '普通感冒症状包括发热'},
            {'url': url, 'snippet': '搜索摘要不应成为证据'},
        ]}
        article.return_value = ('普通感冒科普', [
            '普通感冒的症状包括咳嗽、鼻塞和流涕，症状可能因人而异。',
        ])
        order = []

        def local(question, **options):
            search.assert_not_called()
            order.append('local')
            return {'id': 'local', 'status': 'insufficient', 'question': question}

        result = run_consultation(self.store, question='普通感冒有什么症状？', runner=local,
                                  on_progress=lambda event, **fields: order.append(event))
        self.assertLess(order.index('local'), order.index('web_search'))
        search.assert_called_once_with(
            'https://api.deepseek.com/v1/tools/search',
            headers={'Authorization': 'Bearer fixture'},
            json_body={'query': '感冒 常见症状 site:www.nhc.gov.cn OR site:www.chinacdc.cn OR site:www.beijing.gov.cn',
                       'count': 5})
        article.assert_called_once_with(url)
        self.assertEqual(result['status'], 'guidance')
        self.assertEqual(result['intent_coverage'], {'symptoms': 'supported'})
        self.assertIn(article.return_value[1][0], result['answer'])
        self.assertNotIn('搜索摘要', result['answer'])
        self.assertEqual(result['sources'][0]['source_url'], url)

    @patch('meditouch.public_health.fetch_topic')
    def test_api_first_keeps_public_answer_and_fills_only_local_gap(self, fetch):
        fetch.return_value = ('https://medlineplus.gov/commoncold.html', (
            'Symptoms include: sneezing and coughing.',
        ))

        def local(question, **options):
            fetch.assert_called()
            return {'id': 'local', 'status': 'answered', 'question': question,
                    'sources': [{'id': 'local-source'}], 'answer_sections': [
                        {'title': '常见症状', 'intent': 'symptoms', 'has_evidence': True,
                         'excerpts': [{'quote': 'Duplicate symptoms', 'evidence_id': 'local-source'}]},
                        {'title': '日常护理', 'intent': 'care', 'has_evidence': True,
                         'excerpts': [{'quote': 'Local care', 'evidence_id': 'local-source'}]},
                    ]}

        result = run_consultation(self.store, question='感冒有什么症状，怎么护理？',
                                  runner=local, public_lookup=public_fallback)
        self.assertEqual(result['intent_coverage'], {'symptoms': 'supported', 'care': 'supported'})
        self.assertIn('Local care', result['answer'])
        self.assertNotIn('Duplicate symptoms', result['answer'])
        self.assertEqual({source['id'] for source in result['sources']},
                         {'public-medlineplus-common-cold', 'local-source'})

    @patch('meditouch.public_health.fetch_topic')
    def test_new_topic_resumes_with_source_linked_change(self, fetch):
        fetch.return_value = ('https://medlineplus.gov/cough.html', (
            'Get medical help right away if you have a cough with trouble breathing.',
        ))
        intake = self.run_turn(question='咳嗽怎么办？')
        self.assertEqual(intake['status'], 'needs_clarification')
        first = self.run_turn(session_id=intake['session_id'], revision=intake['revision'],
                              known_context=self.cough_context)
        self.assertEqual(first['followup']['field'], 'public_signal')
        self.assertEqual(fetch.call_args.args[0], 'Cough')
        second = self.run_turn(session_id=first['session_id'], revision=first['revision'],
                               known_context={'public_signal': '有'})
        self.assertIsNone(second['followup'])
        change = second['answer_sections'][0]
        self.assertEqual(change['title'], '这次补充改变了什么')
        self.assertEqual(change['excerpts'][0]['evidence_id'], second['sources'][-1]['id'])

    @patch('meditouch.public_health.fetch_topic')
    def test_no_direct_evidence_or_timeout_keeps_local_result(self, fetch):
        fetch.return_value = ('https://medlineplus.gov/cough.html', (
            'Cough is a common reflex that clears your throat.',
        ))
        result = self.run_turn(question='咳嗽怎么办？', known_context=self.cough_context)
        self.assertEqual(result['status'], 'insufficient')
        self.assertIsNone(result.get('followup'))
        fetch.side_effect = TimeoutError()
        with patch('meditouch.public_evidence_cache.read_snapshot', return_value=None), patch(
                'meditouch.public_health.read_snapshot', return_value=None):
            result = self.run_turn(question='咳嗽怎么办？', known_context=self.cough_context)
        self.assertEqual(result['status'], 'insufficient')
        public_trace = next(event for event in result['trace'] if event['node'] == 'public_knowledge')
        self.assertEqual(public_trace['status'], 'unavailable')

    @patch('meditouch.public_health.fetch_topic')
    def test_medication_and_existing_guidance_do_not_call_public_api(self, fetch):
        for question in ('感冒吃什么药', '浑身肌肉酸痛，喉咙疼什么情况？', '我家孩子拉肚子怎么办？'):
            with self.subTest(question=question):
                result = self.run_turn(question=question)
                self.assertIn(result['status'], {'insufficient', 'guidance', 'needs_clarification'})
        fetch.assert_not_called()
