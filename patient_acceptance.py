"""Five engineering scenarios plus independently specified 40-case flow checks.

Development question checks are not clinical gold labels or model benchmarks.
"""
import argparse
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile

from agent_acceptance import fixture_service, call, FailingService
from agent_graph import run_graph
from consultation import ConsultationStore, run_consultation
from evidence_service import EvidenceService, EvidenceError
from model_gateway import ModelGateway
from patient_context import QUESTIONS
from reviewed_content import attach_reviews

ROOT=Path(__file__).parent


def reviewed_fixture():
    items=fixture_service().all_items('demo');i=items[0]
    record=dict(id='fixture-patient-statement',evidence_id=i['id'],source_snapshot_sha256=i['snapshot_sha256'],
                source_quote=i['raw_quote'],
                patient_text='对虚构人群P，演示资料不支持措施X。',applicability='只适用于虚构工程测试',
                limitations='不对应任何真实疾病、治疗或患者。',reviewer='synthetic-engineering-reviewer',
                reviewed_at=date.today().isoformat(),review_kind='engineering_fixture',status='approved',scope='general_education')
    return EvidenceService(items=attach_reviews(items,records=[record]))


class AcceptanceModel:
    """Deterministic protocol fixture; does not simulate medical expertise."""
    def __init__(self,scenario='normal'):self.calls=0;self.scenario=scenario
    def complete(self,messages,tools=None,tool_choice='auto'):
        self.calls+=1
        user=json.loads(messages[1]['content'])
        if self.calls==1:
            if user['missing_context']:return call('ask_user',{'fields':user['missing_context']})
            return call('search_evidence',{'question':user['question'],'k':3})
        observation=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))
        if observation.get('status')=='error' and observation['error']['retryable'] and self.calls==2:
            return call('search_evidence',{'question':user['question'],'k':3},'retry')
        items=observation.get('items',[])
        if self.scenario=='bad_citation':
            draft={'status':'answer','citations':[{'evidence_id':'fabricated','quote':'fabricated'}]}
        elif items and items[0].get('demo'):
            draft={'status':'answer','citations':[{'evidence_id':items[0]['id']}]}
        else:draft={'status':'insufficient','citations':[]}
        return {'role':'assistant','content':json.dumps(draft,ensure_ascii=False)}


class ForcedFixture:
    def __init__(self,conflict=True):self.service=fixture_service(conflict)
    def search(self,question,k=3,corpus='demo'):
        result=self.service.search('演示措施X',k,'demo');result['query']=question
        return result


class CorruptFixture:
    def search(self,*args,**kwargs):raise EvidenceError('CORPUS_INTEGRITY_FAILED')


def run_five(live=False):
    cases=[('normal','演示措施X的科普信息是什么？','answered',fixture_service()),
           ('clarification','我发烧了怎么办？','needs_clarification',fixture_service()),
           ('insufficient','zzzzunknown','insufficient',EvidenceService(items=[])),
           ('conflict','演示措施X有哪些分歧？','unresolved',fixture_service(True)),
           ('tool_failure','演示措施X原文是什么？','tool_error',FailingService())]
    rows=[]
    for name,question,expected,service in cases:
        model=ModelGateway() if live else AcceptanceModel()
        result=run_graph(question,corpus='demo',service=service,model=model)
        checks={'status':result['status']==expected}
        checks['model_consumed_observation']=result['model_calls']>=2
        if name=='clarification':checks['asked_required_fields']=any(t.get('tool')=='ask_user' for t in result['trace'])
        if name=='insufficient':checks['no_citations']=not result['citations']
        if name=='normal':
            checks.update(source_binding=bool(result.get('citations')),
                          citation_validation=bool(result.get('validation',{}).get('ok')),
                          retained_quote=all(c['quote'] in result['answer'] for c in result['citations']))
        if name=='conflict':checks['both_sides']=all(x in result['answer'] for x in ['demo-new','demo-other'])
        if name=='tool_failure':checks['bounded_retry']=1<=result['tool_calls']<=2
        rows.append(dict(scenario=name,expected=expected,actual=result['status'],checks=checks,
                         passed=all(checks.values()),model_calls=result['model_calls'],tool_calls=result['tool_calls']))
    return dict(mode='live_model_engineering_fixtures' if live else 'scripted_protocol_engineering_fixtures',
                passed=sum(r['passed'] for r in rows),total=len(rows),cases=rows)


def run_development():
    expectations=json.loads((ROOT/'eval_data/patient_flow_expectations.json').read_text(encoding='utf-8'))
    questions={c['id']:c for p in ['common_respiratory_questions.json','hypertension_patient_questions.json']
               for c in json.loads((ROOT/'eval_data'/p).read_text(encoding='utf-8'))['cases']}
    if set(questions)!=set(expectations):raise ValueError('every development question needs an explicit expectation')
    rows=[]
    with tempfile.TemporaryDirectory() as temp:
        store=ConsultationStore(Path(temp)/'acceptance.db');store.initialize()
        for cid,spec in expectations.items():
            case=questions[cid];fixture=spec.get('fixture')
            service=ForcedFixture() if fixture=='conflict' else FailingService() if fixture=='timeout' else (
                    CorruptFixture() if fixture=='integrity' else ForcedFixture(False) if fixture=='bad_citation' else EvidenceService())
            context={k:'不清楚' for k in QUESTIONS} if fixture else {}
            def runner(question,**kwargs):
                return run_graph(question,service=service,model=AcceptanceModel('bad_citation' if fixture=='bad_citation' else 'normal'),**kwargs)
            result=run_consultation(store,question=case['question'],corpus='demo' if fixture in ('conflict','bad_citation') else 'real',
                                    known_context=context,runner=runner)
            checks={'status':result['status']==spec['status'],
                    'required_questions':set(spec.get('missing',[]))<=set(result.get('missing_context',[])),
                    'no_unverified_citation':not result['citations'],
                    'uncertainty_present':bool(result.get('uncertainty'))}
            if spec['status']=='risk_alert':checks['risk_before_model']=result['model_calls']==0 and result['tool_calls']==0
            if spec.get('real_retrieval'):checks['real_retrieval']=bool(result['sources']) and result['tool_calls']>0
            if fixture in ('timeout','integrity'):checks['no_fabricated_citation']=not result['citations']
            if fixture=='bad_citation':checks['draft_blocked']='fabricated' not in result['answer']
            rows.append(dict(id=cid,question=case['question'],expected=spec['status'],actual=result['status'],
                             checks=checks,passed=all(checks.values()),flow=result.get('flow'),missing=result.get('missing_context',[])))
    return dict(mode='real_corpus_plus_explicit_failure_fixtures',passed=sum(r['passed'] for r in rows),total=len(rows),cases=rows)


def evaluate_suite(live=False):
    report=dict(created_at=datetime.now(timezone.utc).isoformat(),
                scope='工程及咨询流程验收；合成问题和显式夹具，非医学金标准、临床安全率或独立模型性能评测',
                five=run_five(live),development=run_development())
    report['passed']=report['five']['passed']==5 and report['development']['passed']==40
    out=ROOT/'data';out.mkdir(exist_ok=True)
    name='patient-acceptance-'+('live' if live else 'offline')
    raw=json.dumps(report,ensure_ascii=False,indent=2)
    for filename in [name+'.json',name+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json']:
        (out/filename).write_text(raw,encoding='utf-8')
    return report


def main(live=False):
    report=evaluate_suite(live)
    print(json.dumps({'passed':report['passed'],'five':report['five'],
                      'development':{'passed':report['development']['passed'],'total':40,
                       'failures':[r for r in report['development']['cases'] if not r['passed']]}},ensure_ascii=True))
    return report['passed']


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    raise SystemExit(0 if main(args.live) else 1)
