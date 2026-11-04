"""Load explicit human-reviewed patient wording tied to exact evidence snapshots.

No records are approved by this module or by an agent tool. Local review identity
is recorded, not authenticated; deployment requires an authorized review workflow.
"""
import copy
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

ROOT=Path(__file__).parent
REGISTRY=ROOT/'data/reviewed-content.json'


def attach_reviews(items,path=REGISTRY,records=None):
    from evidence_service import fingerprint
    if records is None:
        path=Path(path)
        if not path.exists(): return items
        records=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(records,list): raise ValueError('review registry must be a list')
    result=copy.deepcopy(items);by_id={i['id']:i for i in result};seen=set()
    for record in records:
        if not isinstance(record,dict):raise ValueError('review record must be an object')
        if record.get('status')!='approved': continue
        for key in ('id','evidence_id','source_snapshot_sha256','source_quote','patient_text','applicability','limitations','reviewer','reviewed_at','review_kind'):
            if not isinstance(record.get(key),str) or not record[key].strip(): raise ValueError('incomplete review record')
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}',record['id']) or record['id'] in seen: raise ValueError('invalid review id')
        seen.add(record['id'])
        reviewed=date.fromisoformat(record['reviewed_at'])
        if reviewed>date.today(): raise ValueError('future review date')
        if record.get('scope')!='general_education': raise ValueError('only general education is supported')
        if len(record['patient_text'])>2000: raise ValueError('reviewed statement too long')
        item=by_id.get(record['evidence_id'])
        if item is None: raise ValueError('reviewed evidence no longer exists')
        original=next(i for i in items if i['id']==item['id'])
        if original['snapshot_sha256']!=record['source_snapshot_sha256'] or fingerprint(original)!=original['snapshot_sha256']:
            raise ValueError('review no longer matches source')
        if record['source_quote']!=original['raw_quote']: raise ValueError('review quote no longer matches source')
        if record['review_kind']!=('engineering_fixture' if item['demo'] else 'clinical'):
            raise ValueError('review kind/corpus mismatch')
        item.setdefault('reviewed_statements',[]).append(copy.deepcopy(record))
        item['review_status']='approved';item['generation_allowed']=True
        item['review_scope']='general_education_statements_only'
    for item in result:item['snapshot_sha256']=fingerprint(item)
    return result


def statement_catalog(items):
    return {r['id']:(i,r) for i in items for r in i.get('reviewed_statements',[])}


def validate_statements(draft,items,decision,corpus,eligible_ids=None):
    from evidence_service import fingerprint
    failure=lambda code:dict(ok=False,errors=[code],citations=[],statements=[])
    if not isinstance(draft,dict) or set(draft)!={'status','statements'}: return failure('STATEMENT_SCHEMA')
    if draft['status'] not in ('answer','insufficient') or not isinstance(draft['statements'],list): return failure('STATEMENT_SCHEMA')
    if (draft['status']=='answer' and not draft['statements']) or (draft['status']=='insufficient' and draft['statements']):
        return failure('STATEMENT_STATUS_MISMATCH')
    if decision['unresolved']:return failure('UNRESOLVED_CONFLICT')
    if len(draft['statements'])>5:return failure('TOO_MANY_STATEMENTS')
    catalog=statement_catalog(items);citations=[];statements=[];seen=set()
    for ref in draft['statements']:
        if not isinstance(ref,dict) or set(ref)!={'statement_id'} or not isinstance(ref['statement_id'],str): return failure('STATEMENT_SCHEMA')
        sid=ref['statement_id']
        if eligible_ids is not None and sid not in eligible_ids:return failure('STATEMENT_OUT_OF_SCOPE')
        if sid not in catalog or sid in seen:return failure('UNKNOWN_OR_DUPLICATE_STATEMENT')
        seen.add(sid);item,record=catalog[sid]
        if item['id'] in decision['retired']:return failure('RETIRED_CITATION')
        if item['review_status']!='approved' or item['generation_allowed'] is not True:return failure('UNREVIEWED_CITATION')
        if item['demo'] and corpus!='demo':return failure('DEMO_CITATION')
        if fingerprint(item)!=item['snapshot_sha256']:return failure('SNAPSHOT_CHANGED')
        if record['scope']!='general_education' or record['status']!='approved':return failure('REVIEW_SCOPE')
        statements.append(dict(statement_id=sid,text=record['patient_text'],evidence_id=item['id'],
                               applicability=record['applicability'],limitations=record['limitations']))
        citations.append(dict(evidence_id=item['id'],quote=item['raw_quote'],statement_id=sid))
    return dict(ok=True,errors=[],citations=citations,statements=statements,
                scope='已审核患者表述逐条绑定完整来源；不证明与当前问题语义相关或个体适用')


def export_template(path):
    from evidence_service import EvidenceService
    items=EvidenceService(review_path=None).all_items()
    records=[dict(id='review-'+str(n+1),status='pending',evidence_id=i['id'],
                  source_snapshot_sha256=i['snapshot_sha256'],patient_text='',applicability='',limitations='',
                  source_quote=i['raw_quote'],source_title=i['source']['title'],source_url=i['source']['url'],
                  pdf_pages=i['pdf_pages'],reviewer='',reviewed_at='',review_kind='clinical',scope='general_education') for n,i in enumerate(items)]
    path=Path(path)
    if path.exists(): raise ValueError('refusing to overwrite an existing review file')
    path.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'templates':len(records),'approved':0}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['template','check','install']);parser.add_argument('path')
    args=parser.parse_args()
    if args.action=='template':result=export_template(args.path)
    else:
        from evidence_service import EvidenceService
        base=EvidenceService(review_path=None).all_items()
        result={'approved_statements':len(statement_catalog(attach_reviews(base,args.path)))}
        if args.action=='install':
            if not result['approved_statements']:raise ValueError('no approved records to install')
            raw=Path(args.path).read_bytes()
            REGISTRY.parent.mkdir(exist_ok=True)
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            if REGISTRY.exists():(REGISTRY.parent/('reviewed-content-'+stamp+'.backup.json')).write_bytes(REGISTRY.read_bytes())
            incoming=REGISTRY.with_suffix('.incoming.json');incoming.write_bytes(raw);incoming.replace(REGISTRY)
            with (REGISTRY.parent/'reviewed-content-audit.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'event':'local_registry_install','at':stamp,
                                         'approved_records':result['approved_statements']},ensure_ascii=False)+'\n')
    print(json.dumps(result,ensure_ascii=True))
