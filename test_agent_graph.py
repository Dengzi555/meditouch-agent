import importlib.util
import json
import unittest
from copy import deepcopy

from agent_acceptance import ScriptedModel, accept, call, fixture_service
from agent_graph import run_graph
from evidence_service import EvidenceService, adjudicate, validate_answer, normalize_document
from model_gateway import ModelError
from server import SEEDS

HAS_GRAPH=importlib.util.find_spec('langgraph') is not None


class ContractTests(unittest.TestCase):
    def test_contract_keeps_unknown_pages_and_pending(self):
        i=normalize_document(SEEDS[0]);self.assertEqual(i['pdf_pages'],[])
        self.assertFalse(i['generation_allowed']);self.assertEqual(i['review_status'],'pending')
        for k in ('source','raw_quote','review_status','spans','snapshot_sha256'): self.assertIn(k,i)

    def test_top_k_expands_counterevidence(self):
        result=fixture_service(True).search('演示措施X',1,'demo')
        items=result['items']+result['related_items']
        self.assertEqual(len(items),3);self.assertTrue(adjudicate(items)['unresolved'])

    def test_quote_resolves_without_review_but_rejects_truncation(self):
        i=normalize_document(SEEDS[0])
        draft={'status':'answer','citations':[{'evidence_id':i['id'],'quote':i['raw_quote']}]}
        self.assertTrue(validate_answer(draft,[i],adjudicate([i]),'demo')['ok'])
        i=fixture_service().all_items('demo')[0]
        draft['citations']=[{'evidence_id':i['id'],'quote':i['raw_quote'][:-1]}]
        self.assertIn('QUOTE_NOT_COMPLETE_OR_EXACT',validate_answer(draft,[i],adjudicate([i]),'demo')['errors'])

    def test_unknown_date_cannot_be_retired(self):
        docs=deepcopy(SEEDS[:2]);docs[0]['date']='2000-01-01'
        items=[normalize_document(d) for d in docs];items[0]['source']['date']=None
        self.assertTrue(adjudicate(items)['unresolved'])

    def test_real_corpus_locator_integrity(self):
        service=EvidenceService()
        if not service.chunks_path.exists(): self.skipTest('本机真实语料未构建')
        result=service.search('高血压食养指南',3,'real')
        self.assertTrue(result['items'])
        for i in result['items']:
            self.assertTrue(i['pdf_pages']);self.assertTrue(i['source']['url'])
            self.assertEqual(i['review_status'],'pending');self.assertFalse(i['generation_allowed'])


@unittest.skipUnless(HAS_GRAPH,'安装 requirements-agent.txt 后运行 LangGraph 测试')
class GraphTests(unittest.TestCase):
    def test_five_scenarios(self):
        r=accept();self.assertEqual(r['passed'],5,r)

    def test_forged_answer_gets_one_repair_then_blocked(self):
        class BadModel(ScriptedModel):
            def complete(self,messages,tools=None,tool_choice='auto'):
                if self.calls==0: return super().complete(messages,tools,tool_choice)
                self.calls+=1
                return {'role':'assistant','content':json.dumps({'status':'answer','citations':[{'evidence_id':'forged','quote':'错误'}]})}
        r=run_graph('演示措施X',corpus='demo',service=fixture_service(),model=BadModel('normal'))
        self.assertEqual(r['status'],'validation_failed');self.assertEqual(r['model_calls'],3)
        self.assertNotIn('错误',r['answer'])

    def test_real_cough_evidence_answers_without_review_registry(self):
        service=EvidenceService()
        if not service.chunks_path.exists(): self.skipTest('本机真实语料未构建')
        r=run_graph('我咳嗽怎么办',service=service,model=ScriptedModel('normal'),
                    known_context={'population':'成人','symptoms':'咳嗽','duration':'不到一天'})
        self.assertEqual(r['status'],'answered')
        self.assertTrue(r['citations'][0]['evidence_id'].startswith('cq-respiratory-home-monitoring'))

    def test_missing_context_cannot_be_bypassed(self):
        r=run_graph('我可以补钾吗',corpus='demo',service=fixture_service(),model=ScriptedModel('insufficient'))
        self.assertEqual(r['status'],'needs_clarification');self.assertIn('renal_function',r['missing_context'])

    def test_repeated_calls_terminate(self):
        class Repeat:
            def complete(self,*args,**kwargs):return call('search_evidence',{'question':'演示措施X'})
        r=run_graph('演示措施X',corpus='demo',service=fixture_service(),model=Repeat(),max_model_calls=3)
        self.assertEqual(r['status'],'budget_exceeded');self.assertLessEqual(r['model_calls'],3)

    def test_no_tools_cannot_make_answer(self):
        class NoTools:
            def complete(self,*args,**kwargs):return {'role':'assistant','content':'无来源的建议'}
        r=run_graph('演示措施X',corpus='demo',service=fixture_service(),model=NoTools())
        self.assertEqual(r['status'],'insufficient');self.assertNotIn('无来源的建议',r['answer'])

    def test_model_error_is_sanitized(self):
        class Broken:
            def complete(self,*args,**kwargs):raise RuntimeError('secret-from-provider')
        r=run_graph('演示措施X',corpus='demo',service=fixture_service(),model=Broken())
        self.assertEqual(r['status'],'model_error');self.assertNotIn('secret-from-provider',json.dumps(r))


if __name__=='__main__':unittest.main()
