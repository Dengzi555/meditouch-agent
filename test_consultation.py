import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from consultation import ConsultationStore, SessionError, finalize_consultation, run_consultation
from patient_context import INTAKE_FIELDS


class ConsultationTests(unittest.TestCase):
    @patch.dict('os.environ', {'MED_AGENT_GENERAL_HEALTH_FALLBACK': '0'})
    def test_topic_switch_carries_intent_but_not_old_disease(self):
        def runner(question, **options):
            return {'id': 'fixture', 'status': 'insufficient', 'question': question}

        result = run_consultation(self.store, question='新冠的症状是啥', runner=runner)
        for short, effective in (
            ('那带状疱疹呢', '带状疱疹有哪些常见症状？'),
            ('那小孩也一样吗？', '儿童带状疱疹有哪些常见症状？'),
        ):
            with patch('consultation.ModelGateway.complete', return_value={'content': json.dumps({
                'action': 'rewrite', 'text': effective, 'same_case': False,
            })}) as model:
                result = run_consultation(self.store, question=short, append_turn=True,
                                          session_id=result['session_id'], revision=result['revision'],
                                          runner=runner)
            self.assertEqual(json.loads(model.call_args.args[0][-1]['content'])['latest'], short)
            self.assertEqual(result['question'], effective)
            self.assertEqual(result['original_question'], short)
            self.assertEqual(result['intent_coverage'], {'symptoms': 'missing'})

    def test_empty_section_cannot_claim_support(self):
        result = {'status': 'answered', 'answer_sections': [
            {'title': '护理建议', 'intent': 'care', 'has_evidence': True, 'excerpts': []},
        ]}
        finalize_consultation(result, '感冒怎么办？', {}, 'real')
        self.assertEqual(result['intent_coverage'], {'care': 'missing'})
        self.assertFalse(result['answer_sections'][0]['has_evidence'])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=ConsultationStore(Path(self.temp.name)/'test.db');self.store.initialize()

    def test_partial_answer_preserves_care_and_marks_medication_gap(self):
        def local(question, **options):
            return {'id': 'local', 'status': 'answered',
                    'sources': [{'id': 'care-source'}],
                    'answer_sections': [{'title': '日常护理', 'has_evidence': True,
                                         'excerpts': [{'quote': '护理证据原文', 'evidence_id': 'care-source'}]}],
                    'followup': {'field': 'temperature', 'question': '量过体温吗？'}}

        result = run_consultation(self.store, question='感冒怎么办，吃什么药？',
                                  known_context={'temperature': '未测量'}, runner=local)
        self.assertEqual(result['intent_coverage'], {'care': 'supported', 'medication': 'missing'})
        self.assertTrue(result['answer_sections'][0]['excerpts'])
        self.assertFalse(result['answer_sections'][1]['has_evidence'])
        self.assertIsNone(result['followup'])

    @patch.dict('os.environ', {'MED_AGENT_GENERAL_HEALTH_FALLBACK': '0'})
    def test_evidence_exit_rejects_missing_source_even_when_marked_supported(self):
        def local(question, **options):
            return {'id': 'local', 'status': 'answered', 'answer': '未核实草稿',
                    'sources': [{'id': 'unrelated'}],
                    'answer_sections': [{'title': '常见症状', 'intent': 'symptoms',
                                         'has_evidence': True, 'excerpts': [
                                             {'quote': '未核实草稿', 'evidence_id': 'missing'},
                                         ]}]}

        result = run_consultation(self.store, question='未知综合症有哪些症状？', runner=local)
        self.assertEqual(result['status'], 'insufficient')
        self.assertEqual(result['sources'], [])
        self.assertNotIn('未核实草稿', result['answer'])
        self.assertIn({'node': 'evidence_exit', 'status': 'insufficient', 'missing': ['symptoms']},
                      result['trace'])

    def test_partial_reply_persists_and_only_missing_fields_are_asked(self):
        def unexpected(*args,**kwargs): raise AssertionError('no model while waiting')
        first=run_consultation(self.store,question='我发烧了怎么办？',runner=unexpected)
        self.assertEqual(first['status'],'needs_clarification');self.assertEqual(first['model_calls'],0)
        second=run_consultation(self.store,session_id=first['session_id'],revision=first['revision'],
                                known_context={'population':'虚构成人','duration':'两天'},runner=unexpected)
        self.assertNotIn('population',second['missing_context']);self.assertNotIn('duration',second['missing_context'])
        restored=ConsultationStore(self.store.database).view(first['session_id'])
        self.assertEqual(restored['context']['duration']['value'],'两天')
        self.assertEqual(restored['revision'],2)
        self.assertEqual(restored['result']['session_id'],first['session_id'])

    def test_ambiguous_fever_question_asks_intent_before_patient_details(self):
        def unexpected(*args,**kwargs): raise AssertionError('no model while waiting')
        for intent in ('饮食','用药'):
            with self.subTest(intent=intent):
                first=run_consultation(self.store,question='发烧吃什么',runner=unexpected)
                self.assertEqual(first['missing_context'],['fever_intent'])
                last=run_consultation(self.store,session_id=first['session_id'],revision=first['revision'],
                    known_context={'fever_intent':intent},
                    runner=lambda question,**kwargs: {'id':'fixture','question':question,'status':'insufficient'})
                self.assertEqual(last['status'],'insufficient')
                self.assertEqual(last['original_question'],'发烧吃什么')

    def test_complete_reply_resumes_original_task_and_preserves_unknown(self):
        first=run_consultation(self.store,question='我发烧了怎么办？')
        called=[]
        def runner(question,**kwargs):
            called.append((question,kwargs))
            return {'id':'test-run','question':question,'status':'pending_review'}
        context={k:'不清楚' for k in INTAKE_FIELDS};context['symptoms']='发热，无咳嗽'
        last=run_consultation(self.store,session_id=first['session_id'],revision=first['revision'],
                              known_context=context,runner=runner)
        self.assertEqual(last['status'],'pending_review');self.assertEqual(len(called),1)
        self.assertEqual(called[0][0],'我发烧了怎么办？')
        self.assertEqual(called[0][1]['known_context']['risk_factors'],'不清楚')
        self.assertEqual(last['context']['symptoms']['value'],'发热，无咳嗽')

    def test_new_consultation_does_not_inherit_context(self):
        first=run_consultation(self.store,question='我咳嗽怎么办？',known_context={'duration':'两周'})
        second=run_consultation(self.store,question='我发烧了怎么办？')
        self.assertNotEqual(first['session_id'],second['session_id'])
        self.assertNotIn('duration',second['context']);self.assertIn('duration',second['missing_context'])

    def test_clearing_a_previous_answer_requires_it_again(self):
        first=run_consultation(self.store,question='我发烧了怎么办？',known_context={'duration':'两天'})
        second=run_consultation(self.store,session_id=first['session_id'],revision=first['revision'],
                                known_context={'duration':''})
        self.assertNotIn('duration',second['context']);self.assertIn('duration',second['missing_context'])

    def test_stale_revision_and_question_change_are_rejected(self):
        first=run_consultation(self.store,question='我发烧了怎么办？')
        with self.assertRaises(SessionError) as error:
            run_consultation(self.store,session_id=first['session_id'],revision=0)
        self.assertEqual(error.exception.code,'SESSION_REVISION_CONFLICT')
        with self.assertRaises(ValueError):
            run_consultation(self.store,session_id=first['session_id'],revision=1,question='换一个问题')
        self.assertEqual(self.store.view(first['session_id'])['revision'],1)

    def test_busy_lease_and_failure_release(self):
        state,lease=self.store.reserve(question='普通感冒和流感区别')
        with self.assertRaises(SessionError) as error:
            self.store.reserve(session_id=state['id'],revision=0)
        self.assertEqual(error.exception.code,'SESSION_BUSY')
        self.store.release(state['id'],lease)
        def broken(*args,**kwargs): raise RuntimeError('synthetic failure')
        with self.assertRaises(RuntimeError):
            run_consultation(self.store,session_id=state['id'],revision=0,runner=broken)
        self.assertEqual(self.store.read(state['id'])['lease_until'],0)


if __name__=='__main__':unittest.main()
