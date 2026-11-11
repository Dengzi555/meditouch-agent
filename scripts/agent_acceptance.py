"""Five-scenario acceptance. --live uses real model calls with synthetic evidence.

No real medical records or raw credentials are written to reports.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import time
from uuid import uuid4

from meditouch.agent_graph import run_graph
from meditouch.evidence_service import EvidenceService, normalize_document
from meditouch.model_gateway import ModelGateway, ModelError, check_roundtrip
from meditouch.server import SEEDS


def fixture_service(conflict=False):
    docs=deepcopy(SEEDS if conflict else [SEEDS[1]])
    for d in docs:
        d.update(review_status='approved',generation_allowed=True)
    # In-memory engineering fixtures only; never publish/approve real documents.
    return EvidenceService(items=[normalize_document(d) for d in docs])


class FailingService:
    def search(self,*args,**kwargs):
        raise TimeoutError('synthetic failure; raw exception must not be exposed')


def call(name, args, call_id='test-call'):
    return {'role':'assistant','content':None,'tool_calls':[
        {'id':call_id,'type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}]}


class ScriptedModel:
    """Offline graph fixture, explicitly not evidence of model performance."""
    def __init__(self,scenario): self.scenario=scenario; self.calls=0
    def complete(self,messages,tools=None,tool_choice='auto'):
        self.calls+=1
        user=json.loads(messages[1]['content']); q=user['question']
        if self.calls==1:
            if self.scenario=='clarification': return call('ask_user',{'fields':user['missing_context']})
            return call('search_evidence',{'question':q,'k':1})
        observation=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))
        if self.scenario=='tool_failure' and self.calls==2:
            return call('search_evidence',{'question':q,'k':1},'retry-call')
        citations=[]
        status='insufficient'
        retired=set(observation.get('arbitration',{}).get('retired',[]))
        candidates=[item for item in observation.get('items',[])+observation.get('related_items',[])
                    if item['id'] not in retired]
        if self.scenario=='normal' and candidates:
            i=candidates[0]
            citations=[{'evidence_id':i['id'],'quote':i['raw_quote']}];status='answer'
        return {'role':'assistant','content':json.dumps({'status':status,'citations':citations},ensure_ascii=False)}


def accept(live=False):
    cases=[
        ('normal','演示措施X的原文规则是什么？','answered',fixture_service()),
        ('clarification','我可以补钾吗？','needs_clarification',fixture_service()),
        ('insufficient','zzzzunknown','insufficient',EvidenceService(items=[])),
        ('conflict','演示措施X有哪些分歧？','unresolved',fixture_service(True)),
        ('tool_failure','演示措施X的原文规则是什么？','tool_error',FailingService()),
    ]
    report={'mode':'live_model_synthetic_evidence' if live else 'offline_scripted_model',
            'scope':'五类工程场景；合成证据；非医学准确率或临床验证','cases':[]}
    for name,q,expected,service in cases:
        result=run_graph(q,corpus='demo',service=service,
                         model=ModelGateway() if live else ScriptedModel(name))
        tools=[t for t in result['trace'] if t['node']=='tool']
        status_ok=result['status']==expected
        checks={'expected_status':status_ok,'tool_executed':bool(tools),
                'model_received_tool_result':result['model_calls']>=2}
        if name=='normal': checks['validated_citation']=bool(result['validation'] and result['validation']['ok'] and result['citations'])
        if name=='clarification': checks['asked_missing_fields']=any(t['tool']=='ask_user' and t['result']['status']=='needs_clarification' for t in tools)
        if name=='conflict': checks['both_sides_retained']=result['arbitration']['unresolved'] and 'demo-new' in result['answer'] and 'demo-other' in result['answer']
        if name=='tool_failure': checks['bounded_retry']=1<=result['tool_calls']<=2
        report['cases'].append({'scenario':name,'expected':expected,'actual':result['status'],
            'passed':all(checks.values()),'checks':checks,'model_calls':result['model_calls'],
            'tool_calls':result['tool_calls'],'trace':result['trace'],'validation':result['validation']})
    report['passed']=sum(c['passed'] for c in report['cases']);report['total']=len(cases)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');p.add_argument('--ping',action='store_true')
    p.add_argument('--real-preview',action='store_true')
    args=p.parse_args()
    try:
        if args.real_preview:
            result=run_graph('成人高血压食养指南有哪些基本原则？')
            report={'ok':result['status']=='answered' and bool(result['citations']) and result['model_calls']>=2,
                    'status':result['status'],'source_count':len(result['sources']),
                    'model_calls':result['model_calls'],'tool_calls':result['tool_calls'],
                    'scope':'真实本地资料检索、工具往返与原文引用校验。'}
        else:
            report=check_roundtrip() if args.ping else accept(args.live)
        ok=report.get('ok',report.get('passed')==report.get('total'))
    except Exception as e:
        report={'ok':False,'error':str(e) if isinstance(e,ModelError) else type(e).__name__};ok=False
    report['created_at']=time.strftime('%Y-%m-%d %H:%M:%S')
    output=Path(__file__).resolve().parent.parent/'data'/('agent-real-preview.json' if args.real_preview else 'agent-ping.json' if args.ping else 'agent-acceptance-live.json' if args.live else 'agent-acceptance-offline.json')
    output.parent.mkdir(exist_ok=True)
    archive=output.with_name(output.stem+'-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:6]+'.json')
    encoded=json.dumps(report,ensure_ascii=False,indent=2)
    archive.write_text(encoded,encoding='utf-8');output.write_text(encoded,encoding='utf-8')
    summary={k:v for k,v in report.items() if k!='cases'}
    if 'cases' in report: summary['cases']=[{k:v for k,v in c.items() if k!='trace'} for c in report['cases']]
    print(json.dumps(summary,ensure_ascii=True,indent=2))
    raise SystemExit(0 if ok else 1)
