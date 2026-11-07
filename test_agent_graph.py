import importlib.util
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from agent_acceptance import ScriptedModel, accept, call, fixture_service
from agent_graph import run_graph
from evidence_service import EvidenceService, adjudicate, fingerprint, validate_answer, normalize_document
from model_gateway import ModelError
from server import SEEDS

HAS_GRAPH=importlib.util.find_spec('langgraph') is not None


class ContractTests(unittest.TestCase):
    def test_slow_supplement_preserves_other_intent_results(self):
        from threading import Event
        from evidence_service import parallel_supplements

        fast_started = Event()
        release = Event()

        def ranked(query, pool, keep):
            if query == 'slow':
                release.wait(2)
            else:
                fast_started.set()
            return pool, 'fixture'

        try:
            with patch('evidence_service.SUPPLEMENT_TIMEOUT', 0.4):
                results, states = parallel_supplements([
                    ('care', 'slow', [{'id': 'late'}]),
                    ('triage', 'fast', [{'id': 'usable'}]),
                ], ranked)
            self.assertTrue(fast_started.is_set())
            self.assertEqual(results, {'triage': [{'id': 'usable'}]})
            self.assertEqual(states, {'care': 'timeout'})
        finally:
            release.set()

    def test_adaptive_search_uses_rewrite_as_a_second_query(self):
        question = '脑袋有点发沉，怎么回事？'
        rewrite = '头部发沉 原因'
        service = EvidenceService(items=[])
        with patch('evidence_service.hybrid_search', return_value=([], 'fixture')) as search:
            response = service.search(question, adaptive=True, query_hint=rewrite)
        self.assertEqual([call.args[0] for call in search.call_args_list], [question, rewrite])
        self.assertEqual(response['query'], question)

    def test_optional_reranker_runs_before_candidate_cutoff(self):
        from unittest.mock import patch
        from evidence_service import adaptive_hits

        candidates = [{'id': str(i), 'text': '资料正文'} for i in range(5)]
        with patch.dict('os.environ', {'MED_AGENT_RERANK': '1'}), patch(
            'evidence_service.hybrid_search', return_value=(candidates, 'hybrid')
        ) as retrieve, patch('reranking.rerank', return_value=list(reversed(candidates))):
            selected, mode, *_ = adaptive_hits('示例问题', '示例问题', candidates, {})
        self.assertEqual(retrieve.call_args.args[2], 5)
        self.assertEqual([item['id'] for item in selected], ['4', '3', '2'])
        self.assertEqual(mode, 'hybrid+rerank')

    def test_adaptive_search_fills_only_missing_intent(self):
        from unittest.mock import patch
        from evidence_service import adaptive_hits

        question = '普通感冒怎么办，吃什么药？'
        care = {'id': 'care', 'text': '普通感冒期间注意休息。'}
        flu = {'id': 'flu', 'text': '流行性感冒患者应由医生指导用药。'}
        irrelevant = {'id': 'other', 'text': '普通感冒常见表现是鼻塞。'}
        medicine = {'id': 'medicine', 'text': '普通感冒使用复方感冒药有一定不良反应。'}
        with patch('evidence_service.hybrid_search', side_effect=[
            ([care, flu, irrelevant], 'hybrid'), ([medicine], 'hybrid'),
        ]) as search:
            selected, _, _, _, missing, _ = adaptive_hits(
                question, question, [care, flu, irrelevant, medicine], {})
        self.assertEqual([item['id'] for item in selected], ['care', 'medicine'])
        self.assertEqual(missing, [])
        self.assertEqual(search.call_args_list[1].args[1:], ([medicine], 2))

    def test_combined_question_rejects_background_and_keeps_partial_evidence(self):
        from query_plan import requested_intents, retrieval_queries

        question = '感冒咋办，吃啥药？'
        self.assertEqual(retrieval_queries(question, requested_intents(question)), [
            question, '感冒 日常护理', '感冒 用药注意事项',
        ])
        items = []
        for index, quote in enumerate([
            '鼻病毒可导致普通感冒，常见表现包括鼻塞和流涕。',
            '普通感冒期间可以注意休息。',
        ]):
            item = normalize_document(SEEDS[0])
            item.update(id=str(index), demo=False, raw_quote=quote, claims=[])
            item['snapshot_sha256'] = fingerprint(item)
            items.append(item)
        for selected in ([items[0]], items):
            draft = {'status': 'answer', 'citations': [{'evidence_id': item['id']} for item in selected]}
            result = validate_answer(draft, selected, adjudicate(selected), question=question)
            if len(selected) == 1:
                self.assertIn('EVIDENCE_DOES_NOT_ANSWER_INTENT', result['errors'])
            else:
                self.assertTrue(result['ok'])
                self.assertEqual(result['intent_coverage'], {'1': ['care']})

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
