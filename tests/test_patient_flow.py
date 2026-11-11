from copy import deepcopy
from datetime import date
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from meditouch.consultation import ConsultationStore, run_consultation
from meditouch.consultation_flow import risk_matches, route
from meditouch.evidence_service import EvidenceService, adjudicate, fingerprint
from meditouch.reviewed_content import attach_reviews, statement_catalog, validate_statements
from scripts.agent_acceptance import fixture_service


class RoutingTests(unittest.TestCase):
    def test_shared_plan_keeps_negation_and_unknown_temperature(self):
        from meditouch.query_plan import understand_question
        question = '脑袋发沉，身上发冷，没发烧，没拿体温计量，咋回事？'
        plan = understand_question(question, {})
        self.assertEqual(plan.reported, ('头发沉', '畏寒'))
        self.assertEqual(plan.denied, ('发热',))
        self.assertEqual(plan.unknown, ('体温',))
        result = route(question, {}, plan=plan)
        self.assertEqual(result['kind'], 'guidance')
        self.assertEqual(result['guidance']['followup']['field'], 'duration')
        self.assertNotEqual(route('脑袋发沉，不确定有没有怕冷，咋回事？', {})['kind'], 'guidance')

    def test_subjectless_request_for_help_routes_to_risk(self):
        self.assertEqual(route('呼吸困难怎么办？', {})['kind'], 'risk')
        for question in ('没有呼吸困难怎么办？', '如果呼吸困难怎么办？', '呼吸困难是什么意思？'):
            with self.subTest(question=question):
                self.assertNotEqual(route(question, {})['kind'], 'risk')

    def test_current_risk_overrides_missing_and_never_calls_model(self):
        with tempfile.TemporaryDirectory() as temp:
            store=ConsultationStore(Path(temp)/'test.db');store.initialize()
            def forbidden(*args,**kwargs):raise AssertionError('risk must stop before retrieval')
            r=run_consultation(store,question='我发烧后喘不过气，怎么办？',runner=forbidden)
            self.assertEqual(r['status'],'risk_alert');self.assertEqual(r['flow']['risk_level'],'emergency')
            self.assertEqual(r['model_calls'],0);self.assertFalse(r['missing_context']);self.assertTrue(r['risk_references'])

    def test_negation_hypothetical_and_history_are_not_active_alerts(self):
        for q in ['我没有呼吸困难，只是咳嗽','如果我呼吸困难应该怎么办？','我去年呼吸困难，已经缓解了',
                  '呼吸困难是什么意思？','我不清楚有没有呼吸困难']:
            with self.subTest(q=q):self.assertFalse(risk_matches(q,{}))
        self.assertTrue(risk_matches('我没有胸痛，但是现在喘不过气',{}))

    def test_context_risk_and_distinct_exit_routes(self):
        self.assertEqual(route('普通感冒和流感有什么区别',{})['kind'],'education')
        self.assertEqual(route('我发烧了怎么办',{})['kind'],'clarification')
        self.assertEqual(route('告诉我是甲流还是细菌感染',{})['kind'],'insufficient')
        self.assertEqual(route('我有点不舒服',{'associated_symptoms':'现在喘不过气'})['kind'],'risk')


class ReviewedContentTests(unittest.TestCase):
    def record(self,item):
        return dict(id='review-test',status='approved',evidence_id=item['id'],source_snapshot_sha256=item['snapshot_sha256'],
                    source_quote=item['raw_quote'],patient_text='虚构人群P的演示措施说明。',applicability='仅虚构工程测试',
                    limitations='无真实医学意义。',reviewer='synthetic-reviewer',reviewed_at=date.today().isoformat(),
                    review_kind='engineering_fixture',scope='general_education')

    def test_review_binds_source_text_scope_and_exact_statement(self):
        base=fixture_service().all_items('demo');record=self.record(base[0]);items=attach_reviews(base,records=[record])
        draft={'status':'answer','statements':[{'statement_id':'review-test'}]}
        v=validate_statements(draft,items,adjudicate(items),'demo')
        self.assertTrue(v['ok']);self.assertEqual(v['statements'][0]['text'],record['patient_text'])
        self.assertEqual(v['citations'][0]['quote'],base[0]['raw_quote'])
        bad=deepcopy(record);bad['source_quote']='different source'
        with self.assertRaises(ValueError):attach_reviews(base,records=[bad])
        bad=deepcopy(record);bad['source_snapshot_sha256']='0'*64
        with self.assertRaises(ValueError):attach_reviews(base,records=[bad])
        bad=deepcopy(record);bad['scope']='individual_prescription'
        with self.assertRaises(ValueError):attach_reviews(base,records=[bad])

    def test_pending_cannot_approve_and_tampering_or_unknown_output_is_blocked(self):
        base=fixture_service().all_items('demo');record=self.record(base[0])
        pending=deepcopy(record);pending['status']='pending'
        self.assertFalse(statement_catalog(attach_reviews(base,records=[pending])))
        items=attach_reviews(base,records=[record])
        v=validate_statements({'status':'answer','statements':[{'statement_id':'fake'}]},items,adjudicate(items),'demo')
        self.assertFalse(v['ok'])
        items[0]['reviewed_statements'][0]['patient_text']='tampered'
        v=validate_statements({'status':'answer','statements':[{'statement_id':'review-test'}]},items,adjudicate(items),'demo')
        self.assertIn('SNAPSHOT_CHANGED',v['errors'])

    def test_revoked_review_reverts_to_base_and_cross_corpus_review_is_rejected(self):
        base=fixture_service().all_items('demo');record=self.record(base[0]);record['status']='revoked'
        self.assertFalse(statement_catalog(attach_reviews(base,records=[record])))
        record['status']='approved';record['review_kind']='clinical'
        with self.assertRaises(ValueError):attach_reviews(base,records=[record])


@unittest.skipUnless(importlib.util.find_spec('langgraph'),'requires agent environment')
class PatientAcceptanceTests(unittest.TestCase):
    def test_five_scenarios(self):
        from scripts.patient_acceptance import run_five
        five=run_five();self.assertEqual(five['passed'],5,five)

    def test_forty_explicit_expectations_with_local_sources(self):
        from scripts.patient_acceptance import run_development
        from meditouch.evidence_service import EvidenceService
        if not EvidenceService().chunks_path.exists():
            self.skipTest('optional local evidence corpus is not installed')
        development=run_development();self.assertEqual(development['passed'],40,[r for r in development['cases'] if not r['passed']])

    def test_model_cannot_rewrite_a_reviewed_statement(self):
        from meditouch.agent_graph import run_graph
        from scripts.patient_acceptance import reviewed_fixture, AcceptanceModel
        class BadModel(AcceptanceModel):
            def complete(self,*args,**kwargs):
                if self.calls==0:return super().complete(*args,**kwargs)
                self.calls+=1
                return {'role':'assistant','content':json.dumps({'status':'answer','statements':[{'statement_id':'fixture-patient-statement','text':'fabricated'}]})}
        result=run_graph('演示措施X的科普内容',corpus='demo',service=reviewed_fixture(),model=BadModel())
        self.assertEqual(result['status'],'validation_failed');self.assertNotIn('fabricated',result['answer'])


if __name__=='__main__':unittest.main()
