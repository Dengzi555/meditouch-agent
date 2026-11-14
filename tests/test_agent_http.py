"""HTTP integration against isolated storage and a scripted model; no paid calls."""
import importlib.util
from http.cookiejar import CookieJar
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import meditouch.server as server
from scripts.agent_acceptance import ScriptedModel, fixture_service


@unittest.skipUnless(importlib.util.find_spec('langgraph'),'需要 requirements-agent.txt')
class AgentHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        cache = patch.dict('os.environ', {'MED_AGENT_PUBLIC_CACHE_DB': str(Path(self.temp.name) / 'cache.db')})
        cache.start()
        self.addCleanup(cache.stop)
        self.dbpatch=patch.object(server,'DB',Path(self.temp.name)/'test.db')
        self.dbpatch.start();self.addCleanup(self.dbpatch.stop);server.init()
        class Quiet(server.Handler):
            def log_message(self,*args):pass
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),Quiet)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)
        self.url='http://127.0.0.1:'+str(self.http.server_port)

    def close(self):
        self.http.shutdown();self.http.server_close();self.thread.join(timeout=3)

    def post(self,path,body):
        request=urllib.request.Request(self.url+path,json.dumps(body).encode(),{'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=8) as response:return json.load(response)

    def test_agent_route_validates_and_persists_citations(self):
        scripted=ScriptedModel('normal')
        with patch('meditouch.agent_graph.EvidenceService',return_value=fixture_service()), patch(
            'meditouch.model_gateway.ModelGateway.complete',side_effect=scripted.complete):
            result=self.post('/api/agent',{'question':'演示措施X','corpus':'demo'})
        self.assertEqual(result['status'],'answered');self.assertTrue(result['validation']['ok'])
        with urllib.request.urlopen(self.url+'/api/session/'+result['session_id']) as response:
            saved=json.load(response)['result']
        self.assertEqual(saved['id'],result['id']);self.assertTrue(saved['citations'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.url+'/api/runs')
        self.assertEqual(error.exception.code,404)

    def test_pharmacy_entry_uses_b_side_role_and_rejects_forged_clearance(self):
        scripted = ScriptedModel('normal')
        with patch('meditouch.agent_graph.EvidenceService', return_value=fixture_service()), patch(
            'meditouch.model_gateway.ModelGateway.complete', side_effect=scripted.complete):
            result = self.post('/api/pharmacy/copilot', {
                'question': '演示措施X', 'corpus': 'demo', 'workspace': {'store_id': 'store-A'},
            })
        self.assertEqual(result['audience'], 'pharmacy_intern')
        self.assertFalse(result['pharmacy_workflow']['handoff']['sale_authorized'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('/api/pharmacy/copilot', {'question': '找药',
                      'workspace': {'store_id': 'A', 'sale_authorized': True}})
        self.assertEqual(error.exception.code, 400)

    def test_pharmacy_endpoint_runs_configured_inventory_and_mandatory_safety(self):
        from sqlmodel import Session
        from meditouch.inventory_models import DrugInventory, create_inventory_engine, create_inventory_tables
        path = Path(self.temp.name) / 'stock.db'
        engine = create_inventory_engine('sqlite:///' + path.as_posix())
        create_inventory_tables(engine)
        with Session(engine) as session:
            session.add(DrugInventory(name='演示药', stock=2, shelf_location='B2'))
            session.commit()
        engine.dispose()
        with patch.dict('os.environ', {'MED_AGENT_INVENTORY_DB': str(path),
                                      'MED_AGENT_INVENTORY_STORE_ID': 'store-A'}), patch(
            'meditouch.pharmacy_generation.ModelGateway.complete',
            side_effect=lambda messages, *args, **kwargs: {'role': 'assistant', 'content': json.dumps(
                {'intent': 'find_drug', 'symptoms': [], 'target_drugs': ['演示药'], 'customer_tags': ['标签甲']}
                if 'query' in json.loads(messages[1]['content']) else {'script_id': 'ask_history'})},
        ):
            result = self.post('/api/pharmacy/copilot', {
                'question': '查演示药', 'workspace': {'store_id': 'store-A', 'task': 'drug_lookup',
                'customer': {'requested_drug': '演示药', 'customer_tags': ['标签甲']}},
            })
            from meditouch.pharmacy_graph import inventory_engine_for_store
            inventory_engine_for_store('store-A').dispose()
        self.assertEqual(result['status'], 'inventory_candidates')
        self.assertEqual(result['safety_status'], 'unknown')
        self.assertIn('请人工核对病史', result['answer'])
        self.assertEqual([event['node'] for event in result['trace']],
                         ['query_understanding', 'inventory_node', 'safety_node', 'generator_node'])

    def test_unrecognized_general_intents_use_the_same_text_route(self):
        for question in ('你好', '初次见面，我能请你搭把手做些什么？', '彩虹有哪几种颜色？'):
            with self.subTest(question=question), patch(
                'meditouch.model_gateway.ModelGateway.complete',
                side_effect=[
                    {'role': 'assistant', 'content': 'GENERAL'},
                    {'role': 'assistant', 'content': '可以，告诉我你想了解什么。'},
                ],
            ) as complete:
                result = self.post('/api/agent', {'question': question})
                self.assertEqual(result['status'], 'general_text')
                self.assertEqual(result['model_calls'], 2)
                self.assertEqual(result['tool_calls'], 0)
                self.assertEqual(result['trace'][0]['intent'], 'GENERAL')
                self.assertEqual(complete.call_count, 2)

    def test_free_text_medical_followup_keeps_prior_symptoms(self):
        first = self.post('/api/agent', {'question': '我现在呼吸困难怎么办？',
                                         'known_context': {'duration': '刚开始'}})
        with patch('meditouch.model_gateway.ModelGateway.complete', return_value={'content': json.dumps({
            'action': 'rewrite', 'text': '我现在呼吸困难，要不要去医院？', 'same_case': True,
        })}):
            followup = self.post('/api/agent', {
                'question': '刚才那个要不要去医院？', 'append_turn': True,
                'session_id': first['session_id'], 'revision': first['revision'],
            })
        self.assertEqual(followup['status'], 'risk_alert')
        self.assertEqual(followup['context']['duration']['value'], '刚开始')
        self.assertEqual(followup['original_question'], '刚才那个要不要去医院？')

    def test_new_messages_share_session_and_restore_prior_turns(self):
        with patch('meditouch.model_gateway.ModelGateway.complete', side_effect=[
            {'role': 'assistant', 'content': '这是回答。'},
            {'content': '{"action":"keep","same_case":false}'},
            {'role': 'assistant', 'content': '这是回答。'},
        ]) as model:
            first = self.post('/api/agent', {'question': '写一段故事'})
            second = self.post('/api/agent', {
                'question': '续写这篇小说', 'append_turn': True,
                'session_id': first['session_id'], 'revision': first['revision'],
            })
            self.assertEqual(model.call_args.args[0][-3]['content'], '写一段故事')
        self.assertEqual(second['session_id'], first['session_id'])
        self.assertEqual(second['revision'], first['revision'] + 1)
        self.assertEqual(second['prior_turns'][0]['question'], '写一段故事')
        with urllib.request.urlopen(self.url+'/api/session/'+second['session_id']) as response:
            saved = json.load(response)
        self.assertEqual(saved['question'], '续写这篇小说')
        self.assertEqual(saved['prior_turns'][0]['answer'], '这是回答。')

    def test_general_writing_uses_model_without_tools_and_preserves_risk_priority(self):
        with patch('meditouch.model_gateway.ModelGateway.complete', return_value={
            'role': 'assistant', 'content': '第一幕：月亮从海面升起。',
        }) as complete:
            for question in ('构思一篇五百字小说', '列一个项目汇报 PPT 大纲', '润色这封邮件：会议改到周五'):
                result = self.post('/api/agent', {'question': question})
                self.assertEqual(result['status'], 'general_text')
                self.assertEqual(result['tool_calls'], 0)
                self.assertEqual(result['citations'], [])
                self.assertEqual(complete.call_args.args[0][-1]['content'], question)
                self.assertEqual(len(complete.call_args.args), 1)
            risk = self.post('/api/agent', {'question': '我胸口疼，帮我写封请假邮件'})
            self.assertEqual(risk['status'], 'risk_alert')
            self.assertEqual(complete.call_count, 3)

    def test_legacy_stream_is_retired_instead_of_serving_ndjson(self):
        request = urllib.request.Request(
            self.url + '/api/agent/stream',
            json.dumps({'question': '演示措施X', 'corpus': 'demo'}).encode(),
            {'Content-Type': 'application/json'},
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=8)
        self.assertEqual(error.exception.code, 410)

    def test_measured_fever_answers_first_and_preserves_temperature_on_followup(self):
        first = self.post('/api/agent', {'question': '39度高烧怎么办'})
        self.assertEqual(first['status'], 'guidance')
        self.assertEqual(first['answer_sections'][0]['title'], '什么时候就医')
        self.assertEqual(first['followup']['field'], 'population')
        self.assertNotIn('布洛芬', first['answer'])
        resumed = self.post('/api/agent', {'session_id': first['session_id'],
            'revision': first['revision'], 'known_context': {'population': '成人'}})
        self.assertEqual(resumed['original_question'], '39度高烧怎么办')
        self.assertEqual(resumed['followup']['field'], 'duration')
        self.assertEqual(resumed['answer_sections'][-1]['title'], '退烧药怎么考虑')

    def test_child_diarrhea_followup_moves_care_to_the_front(self):
        first = self.post('/api/agent', {'question': '孩子拉肚子怎么办？'})
        self.assertEqual(first['followup']['field'], 'associated_symptoms')
        self.assertEqual(first['answer_sections'][2]['title'], '用药注意事项')
        resumed = self.post('/api/agent', {
            'session_id': first['session_id'], 'revision': first['revision'],
            'known_context': {'associated_symptoms': '尿量明显减少'},
        })
        self.assertEqual(resumed['answer_sections'][0]['title'], '什么时候就医')
        self.assertIn('尿量减少提示可能脱水', resumed['answer'])
        self.assertIsNone(resumed['followup'])

    def test_invalid_context_and_k_are_client_errors(self):
        for path,body in [('/api/agent',{'question':'test','known_context':[]}),
                          ('/api/evidence/search',{'question':'test','k':True})]:
            with self.assertRaises(urllib.error.HTTPError) as cm:self.post(path,body)
            self.assertEqual(cm.exception.code,400)

    def test_consultation_partial_resume_restore_and_final_graph(self):
        first=self.post('/api/agent',{'question':'我发烧了怎么办？','corpus':'demo'})
        self.assertEqual(first['status'],'needs_clarification');self.assertEqual(first['model_calls'],0)
        second=self.post('/api/agent',{'session_id':first['session_id'],'revision':first['revision'],
                                     'known_context':{'population':'虚构成人','duration':'两天'}})
        self.assertNotIn('duration',second['missing_context'])
        with urllib.request.urlopen(self.url+'/api/session/'+first['session_id']) as response:restored=json.load(response)
        self.assertEqual(restored['context']['duration']['value'],'两天')
        self.assertEqual(restored['corpus'],'demo')
        context={'symptoms':'发热','temperature':'未测量','temperature_method':'未测量','associated_symptoms':'不清楚',
                 'risk_factors':'不清楚','medications':'未用药；过敏史不清楚'}
        scripted=ScriptedModel('normal')
        with patch('meditouch.agent_graph.EvidenceService',return_value=fixture_service()),patch(
                'meditouch.model_gateway.ModelGateway.complete',side_effect=scripted.complete):
            last=self.post('/api/agent',{'session_id':first['session_id'],'revision':second['revision'],
                                        'known_context':context})
        self.assertEqual(last['session_id'],first['session_id'])
        self.assertEqual(last['original_question'],'我发烧了怎么办？')
        self.assertEqual(last['status'],'insufficient');self.assertGreater(last['tool_calls'],0)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post('/api/agent',{'session_id':first['session_id'],'revision':first['revision']})
        self.assertEqual(cm.exception.code,409)

    def test_risk_http_response_and_restore_do_not_wait_for_intake(self):
        with patch('meditouch.agent_graph.run_graph',side_effect=AssertionError('risk must not invoke model')):
            result=self.post('/api/agent',{'question':'我现在喘不过气，怎么办？'})
        self.assertEqual(result['status'],'risk_alert');self.assertEqual(result['model_calls'],0)
        with urllib.request.urlopen(self.url+'/api/session/'+result['session_id']) as response:restored=json.load(response)
        self.assertEqual(restored['result']['status'],'risk_alert');self.assertFalse(restored['missing_context'])

    def test_patient_acceptance_endpoint_uses_offline_suite(self):
        with patch('scripts.patient_acceptance.evaluate_suite',return_value={'passed':True,'scope':'fixture'}) as evaluate:
            result=self.post('/api/patient/evaluate',{})
        self.assertTrue(result['passed']);evaluate.assert_called_once_with(False)

    def test_sore_throat_guidance_answers_before_contextual_followup(self):
        first = self.post('/api/agent', {'question': '我浑身疼，喉咙疼'})
        self.assertEqual(first['status'], 'guidance')
        self.assertIn('流感', first['answer'])
        self.assertEqual(first['followup']['options'], ['发热', '没有发烧', '头痛', '咳嗽', '鼻塞或流鼻涕'])
        self.assertEqual(first['missing_context'], [])
        self.assertTrue(first['guidance_references'])
        second = self.post('/api/agent', {
            'session_id': first['session_id'], 'revision': first['revision'],
            'known_context': {'associated_symptoms': '发热'},
        })
        self.assertEqual(second['followup']['field'], 'temperature')
        self.assertIn('你还提到了发热', second['answer'])
        narrowed = self.post('/api/agent', {
            'session_id': first['session_id'], 'revision': second['revision'],
            'known_context': {'associated_symptoms': '发热、头痛', 'temperature': '39℃ 或以上'},
        })
        self.assertIn('更符合流感的常见表现', narrowed['answer_sections'][0]['excerpts'][0]['quote'])
        self.assertIn('新冠', narrowed['answer'])
        self.assertEqual(narrowed['followup']['field'], 'duration')
        updated = self.post('/api/agent', {
            'session_id': first['session_id'], 'revision': narrowed['revision'],
            'known_context': {'associated_symptoms': '没有发烧、头痛'},
        })
        self.assertEqual(updated['status'], 'needs_clarification')
        self.assertEqual(updated['clarification_card']['field'], 'fever_status')
        corrected = self.post('/api/agent', {
            'session_id': first['session_id'], 'revision': updated['revision'],
            'known_context': {'fever_status': '此前发热，目前已退热'},
        })
        self.assertIn('少了一个提示典型流感的线索', corrected['answer'])
        self.assertIn('仍无法判断炎症是否仅限于咽喉', corrected['answer'])
        self.assertIsNone(corrected['followup'])
        sore_throat = self.post('/api/agent', {'question': '我喉咙疼'})
        self.assertIn('吞咽困难', sore_throat['followup']['options'])
        self.assertNotIn('头痛', sore_throat['followup']['options'])
        risk = self.post('/api/agent', {'question': '喉咙疼，呼吸困难'})
        self.assertEqual(risk['status'], 'risk_alert')

    def test_account_history_and_consultation_memory_are_isolated(self):
        def client():
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

            def request(path, body=None):
                data = None if body is None else json.dumps(body).encode()
                req = urllib.request.Request(self.url + path, data, {'Content-Type': 'application/json'})
                with opener.open(req, timeout=8) as response:
                    return json.load(response)

            return request

        alice, bob, guest = client(), client(), client()
        alice_login = {'username': 'demo_alice', 'password': 'fixture-alice'}
        alice('/api/account/register', alice_login)
        bob('/api/account/register', {'username': 'demo_bob', 'password': 'fixture-bob'})
        first = alice('/api/agent', {
            'question': '我发烧了怎么办？', 'known_context': {'duration': '两天'},
        })
        session_path = '/api/session/' + first['session_id']
        self.assertEqual(alice('/api/sessions')[0]['id'], first['session_id'])
        self.assertEqual(bob('/api/sessions'), [])
        for outsider in (bob, guest):
            for path, body in (
                (session_path, None),
                ('/api/agent', {'session_id': first['session_id'], 'revision': first['revision']}),
            ):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    outsider(path, body)
                self.assertEqual(error.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as error:
            guest('/api/runs')
        self.assertEqual(error.exception.code,404)
        alice('/api/account/logout', {})
        self.assertIsNone(alice('/api/account')['account'])
        alice('/api/account/login', alice_login)
        self.assertEqual(alice(session_path)['context']['duration']['value'], '两天')
        manage = '/api/sessions/manage'
        first_id = first['session_id']
        second_id = alice('/api/agent', {'question': '我咳嗽怎么办？'})['session_id']
        alice(manage, {'action': 'rename', 'ids': [first_id], 'value': '发热咨询'})
        alice(manage, {'action': 'pin', 'ids': [first_id], 'value': True})
        self.assertEqual(alice('/api/sessions')[0], {'id': first_id, 'title': '发热咨询', 'pinned': True})
        self.assertEqual(alice(session_path)['question'], first['question'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            bob(manage, {'action': 'delete', 'ids': [first_id]})
        self.assertEqual(error.exception.code, 404)
        alice(manage, {'action': 'pin', 'ids': [first_id], 'value': False})
        self.assertEqual(alice('/api/sessions')[0]['id'], second_id)
        alice(manage, {'action': 'delete', 'ids': [first_id, second_id]})
        self.assertEqual(alice('/api/sessions'), [])


if __name__=='__main__':unittest.main()
