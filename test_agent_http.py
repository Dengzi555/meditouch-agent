"""HTTP integration against isolated storage and a scripted model; no paid calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import server
from agent_acceptance import ScriptedModel, fixture_service


@unittest.skipUnless(importlib.util.find_spec('langgraph'),'需要 requirements-agent.txt')
class AgentHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
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
        with patch('agent_graph.EvidenceService',return_value=fixture_service()), patch(
            'model_gateway.ModelGateway.complete',side_effect=scripted.complete):
            result=self.post('/api/agent',{'question':'演示措施X','corpus':'demo'})
        self.assertEqual(result['status'],'answered');self.assertTrue(result['validation']['ok'])
        with urllib.request.urlopen(self.url+'/api/runs') as response:runs=json.load(response)
        self.assertEqual(runs[0]['id'],result['id']);self.assertTrue(runs[0]['citations'])

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
        with patch('agent_graph.EvidenceService',return_value=fixture_service()),patch(
                'model_gateway.ModelGateway.complete',side_effect=scripted.complete):
            last=self.post('/api/agent',{'session_id':first['session_id'],'revision':second['revision'],
                                        'known_context':context})
        self.assertEqual(last['session_id'],first['session_id'])
        self.assertEqual(last['original_question'],'我发烧了怎么办？')
        self.assertEqual(last['status'],'insufficient');self.assertGreater(last['tool_calls'],0)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post('/api/agent',{'session_id':first['session_id'],'revision':first['revision']})
        self.assertEqual(cm.exception.code,409)

    def test_risk_http_response_and_restore_do_not_wait_for_intake(self):
        with patch('agent_graph.run_graph',side_effect=AssertionError('risk must not invoke model')):
            result=self.post('/api/agent',{'question':'我现在喘不过气，怎么办？'})
        self.assertEqual(result['status'],'risk_alert');self.assertEqual(result['model_calls'],0)
        with urllib.request.urlopen(self.url+'/api/session/'+result['session_id']) as response:restored=json.load(response)
        self.assertEqual(restored['result']['status'],'risk_alert');self.assertFalse(restored['missing_context'])

    def test_patient_acceptance_endpoint_uses_offline_suite(self):
        with patch('patient_acceptance.evaluate_suite',return_value={'passed':True,'scope':'fixture'}) as evaluate:
            result=self.post('/api/patient/evaluate',{})
        self.assertTrue(result['passed']);evaluate.assert_called_once_with(False)


if __name__=='__main__':unittest.main()
