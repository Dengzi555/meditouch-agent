"""SQLite-backed consultation slots and bounded leases, separate from model memory."""
import json
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from evidence_service import validate_request
from patient_context import QUESTIONS, missing_context, validate_context
from consultation_flow import route, stopped_result


class SessionError(Exception):
    def __init__(self,code,status): self.code=code;self.status=status;super().__init__(code)


class ConsultationStore:
    def __init__(self,database): self.database=Path(database)

    def initialize(self):
        with closing(sqlite3.connect(self.database)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS consultations (id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def read(self,session_id):
        if not isinstance(session_id,str) or not re.fullmatch('[a-f0-9]{32}',session_id):
            raise ValueError('无效 session_id')
        with closing(sqlite3.connect(self.database)) as db:
            row=db.execute('SELECT body FROM consultations WHERE id=?',(session_id,)).fetchone()
        if not row: raise SessionError('SESSION_NOT_FOUND',404)
        return json.loads(row[0])

    def view(self,session_id):
        state=self.read(session_id)
        decision=route(state['question'],values(state))
        return dict(session_id=state['id'],revision=state['revision'],question=state['question'],
                    corpus=state['corpus'],context=state['slots'],status=state['status'],
                    result=state.get('result'),missing_context=decision['missing_fields'],flow=decision,
                    questions=QUESTIONS,storage_scope='仅本机咨询状态；用户声明未核实，非长期健康档案')

    def reserve(self,question=None,corpus=None,known_context=None,session_id=None,revision=None):
        updates=validate_context({} if known_context is None else known_context,keep_empty=True)
        if session_id is not None and (not isinstance(session_id,str) or not re.fullmatch('[a-f0-9]{32}',session_id)):
            raise ValueError('无效 session_id')
        now=time.time();lease=uuid4().hex
        with closing(sqlite3.connect(self.database,timeout=5)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            if session_id:
                if not isinstance(session_id,str) or not re.fullmatch('[a-f0-9]{32}',session_id):
                    raise ValueError('无效 session_id')
                row=db.execute('SELECT body FROM consultations WHERE id=?',(session_id,)).fetchone()
                if not row: raise SessionError('SESSION_NOT_FOUND',404)
                state=json.loads(row[0])
                if type(revision) is not int or revision!=state['revision']:
                    raise SessionError('SESSION_REVISION_CONFLICT',409)
                if state.get('lease_until',0)>now: raise SessionError('SESSION_BUSY',409)
                if question is not None and question!=state['question']:
                    raise ValueError('本次咨询保留原问题；更换问题请新建咨询')
                if corpus is not None and corpus!=state['corpus']: raise ValueError('不能更换咨询语料')
            else:
                validate_request(question)
                if corpus is None: corpus='real'
                if corpus not in ('real','demo'): raise ValueError('corpus 必须为 real 或 demo')
                state=dict(id=uuid4().hex,question=question,corpus=corpus,slots={},revision=0,
                           history=[],created_at=now)
            for key,value in updates.items():
                if value: state['slots'][key]=dict(value=value,source='user_declaration',updated_at=now)
                else: state['slots'].pop(key,None)
            state.update(lease=lease,lease_until=now+180,status='running',updated_at=now)
            db.execute('INSERT OR REPLACE INTO consultations VALUES (?,?)',(state['id'],json.dumps(state,ensure_ascii=False)))
        return state,lease

    def finish(self,state,lease,result):
        with closing(sqlite3.connect(self.database,timeout=5)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM consultations WHERE id=?',(state['id'],)).fetchone()
            current=json.loads(row[0])
            if current.get('lease')!=lease: raise SessionError('SESSION_LEASE_EXPIRED',409)
            current.update(revision=current['revision']+1,status=result['status'],lease_until=0,
                           result=result,updated_at=time.time())
            current['history'].append(dict(revision=current['revision'],run_id=result['id'],status=result['status']))
            current['history']=current['history'][-50:]
            result.update(session_id=current['id'],revision=current['revision'],
                          context=current['slots'],original_question=current['question'])
            # result and current reference one another only through JSON-safe slot data.
            db.execute('UPDATE consultations SET body=? WHERE id=?',(json.dumps(current,ensure_ascii=False),current['id']))
        return result

    def release(self,session_id,lease):
        with closing(sqlite3.connect(self.database)) as db,db:
            row=db.execute('SELECT body FROM consultations WHERE id=?',(session_id,)).fetchone()
            state=json.loads(row[0])
            if state.get('lease')==lease:
                state.update(lease_until=0,status='interrupted')
                db.execute('UPDATE consultations SET body=? WHERE id=?',(json.dumps(state,ensure_ascii=False),session_id))


def values(state): return {k:v['value'] for k,v in state['slots'].items()}


def run_consultation(store,*,question=None,corpus=None,known_context=None,session_id=None,revision=None,runner=None):
    if runner is None:
        from agent_graph import run_graph
        runner=run_graph
    state,lease=store.reserve(question,corpus,known_context,session_id,revision)
    try:
        context=values(state);decision=route(state['question'],context);missing=decision['missing_fields']
        if decision['kind'] in ('risk','insufficient'):
            result=stopped_result(state['question'],decision)
        elif missing:
            # Waiting for user input is a host transition, requiring no model call.
            result=dict(id=uuid4().hex,question=state['question'],status='needs_clarification',
                answer=('' if missing==['fever_intent'] else '请补充以下信息，可以填写“不清楚”或“未测量”。这些信息仅用于当前咨询，不据此确诊。\n\n')+
                       '\n\n'.join(QUESTIONS[k] for k in missing),
                missing_context=missing,clarification_questions={k:QUESTIONS[k] for k in missing},
                engine='结构化咨询 · 等待补充',sources=[],citations=[],error=None,
                trace=[{'node':'intake','status':'waiting','missing_fields':missing}],
                model_calls=0,tool_calls=0,latency_ms=0,flow=decision,created_at=time.strftime('%Y-%m-%d %H:%M:%S'))
        else:
            result=runner(state['question'],corpus=state['corpus'],known_context=context)
            result['flow']=decision
        result.setdefault('uncertainty',[])
        result['uncertainty'] += ['用户声明尚未独立核实；信息完整不代表可确诊或治疗适用。']
        unknown=[k for k,v in context.items() if re.search(r'不清楚|不知道|未测|没有测|没测',v)]
        if unknown:result['uncertainty'].append('仍未知的条件：'+'、'.join(unknown))
        return store.finish(state,lease,result)
    except Exception:
        store.release(state['id'],lease)
        raise
