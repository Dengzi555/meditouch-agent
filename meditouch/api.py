"""FastAPI application serving the real pharmacy SSE graph and built React frontend."""
import os
import json
import sqlite3
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

import anyio
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool
from sqlalchemy.exc import SQLAlchemyError

from meditouch.risk import risk_matches
from meditouch.settings import load_local_env
from meditouch.settings_routes import settings_router
from meditouch.model_gateway import ModelGateway
from meditouch.inventory_models import inventory_is_mock
from meditouch.inventory_management import management_router
from meditouch.chat_storage import ChatStore, ChatNotFound, ChatBusy
from meditouch.pharmacy_context import validate_workspace
from meditouch.pharmacy_graph import (
    build_pharmacy_graph, inventory_engine_for_store, inventory_write_engine_for_store, prepare_inventory_state,
)
from meditouch.pharmacy_ui import encode_sse, graph_ui_events, ui_event
from meditouch.inventory_import import (
    ConfirmImport, DuplicateInventory, MAX_IMAGE_BYTES, VisionError, VisionGateway,
    extract_prefill, import_confirmed, vision_configured as vision_is_configured,
)

ROOT = Path(__file__).resolve().parent.parent
DEV_ORIGINS = ['http://localhost:5173', 'http://127.0.0.1:5173']


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=2000)


class CopilotRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=20000)
    workspace: dict
    history: list[HistoryMessage] = Field(default_factory=list, max_length=20)
    session_id: str | None = Field(default=None, min_length=1, max_length=64)
    turn_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator('question')
    @classmethod
    def question_not_blank(cls, value):
        if not value.strip():
            raise ValueError('请输入问题')
        return value

    @field_validator('workspace')
    @classmethod
    def valid_workspace(cls, value):
        return validate_workspace(value)


def pharmacy_event_stream(body, engine, *, model, on_complete=None, data_mode='real'):
    """Blocking graph iterator, consumed in a worker thread by the async SSE route."""
    run_id, seq, started = uuid4().hex, 1, time.monotonic()
    yield encode_sse(ui_event(run_id, seq, 'progress', {
        'stage': 'intake', 'phase': 'started', 'attempt': 0, 'label': '正在识别问题与顾客信息',
        'inventory_data_mode': data_mode}))
    try:
        initial = prepare_inventory_state(body.question, body.workspace, model=model,
            history=[item.model_dump() for item in body.history])
        initial['inventory_data_mode'] = data_mode
        seq += 1
        yield encode_sse(ui_event(run_id, seq, 'progress', {
            'stage': 'understanding', 'phase': 'completed',
            'label': '已识别：' + {'find_drug': '找药咨询', 'check_safety': '用药风险核对',
                'emergency': '急症风险', 'chitchat': '通用交流'}.get(initial.get('intent'), '待人工确认'),
            'intent': initial.get('intent'), 'symptoms': initial.get('symptoms', []),
            'medical_topics': initial.get('medical_topics', []),
            'target_drugs': initial.get('target_drugs', []),
            'denied_symptoms': initial.get('denied_symptoms', []),
        }))
        compiled = build_pharmacy_graph(engine, model=model, deadline=started + 90)
        for event in graph_ui_events(compiled, initial, run_id, seq_start=seq, on_complete=on_complete):
            seq = event['seq']
            yield encode_sse(event)
    except Exception:
        yield encode_sse(ui_event(run_id, seq + 1, 'error', {
            'code': 'RUN_FAILED', 'message': '处理未完成，请人工核对库存和病史', 'retryable': False}))


def create_app(*, engine_resolver=None, model_factory=None, static_dir=None,
               write_engine_resolver=None, vision_factory=None, settings_store=None,
               chat_store=None, identity_resolver=None):
    app = FastAPI(title='医点通 · 药店 Copilot', version='0.3.0')
    app.include_router(settings_router(settings_store))
    resolve_engine = engine_resolver or inventory_engine_for_store
    resolve_writer = write_engine_resolver or inventory_write_engine_for_store
    new_model = model_factory or ModelGateway
    chats = chat_store if chat_store is not None else ChatStore()
    dist = Path(static_dir) if static_dir is not None else ROOT / 'web' / 'dist'
    app.add_middleware(CORSMiddleware, allow_origins=DEV_ORIGINS,
                       allow_methods=['GET', 'POST'], allow_headers=['Content-Type', 'Accept'],
                       expose_headers=['X-Inventory-Data-Mode'])

    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'transport': 'sse', 'schema_version': 'pharmacy.ui.v1'}

    @app.get('/api/pharmacy/config')
    def config():
        store_id, path = os.getenv('MED_AGENT_INVENTORY_STORE_ID'), os.getenv('MED_AGENT_INVENTORY_DB')
        ready = bool(store_id and path and Path(path).is_file())
        data_mode = 'unconfigured'
        if ready:
            try:
                engine = resolve_engine(store_id)
                if engine is None:
                    ready = False
                else:
                    data_mode = 'mock' if inventory_is_mock(engine) else 'real'
            except (ValueError, SQLAlchemyError):
                ready, data_mode = False, 'unavailable'
        return {'store_id': store_id or '', 'inventory_ready': ready, 'inventory_data_mode': data_mode,
                'model_configured': bool(os.getenv('MED_AGENT_API_KEY')),
                'vision_configured': vision_factory is not None or vision_is_configured()}

    def check_origin(request):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/') and origin not in DEV_ORIGINS:
            raise HTTPException(403, '拒绝跨来源请求')

    app.include_router(management_router(resolve_writer, check_origin))

    def chat_scope(request, store_id):
        # This demo identity is server-owned, not a client-selectable user ID.
        owner = identity_resolver(request) if identity_resolver else 'local-demo'
        if not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
            raise HTTPException(401, '请先登录')
        configured = os.getenv('MED_AGENT_INVENTORY_STORE_ID')
        if not store_id.strip() or len(store_id) > 100 or configured and configured != store_id:
            raise HTTPException(403, '门店不匹配')
        return owner, store_id

    class CreateChat(BaseModel):
        model_config = ConfigDict(extra='forbid')
        store_id: str = Field(min_length=1, max_length=100)

    @app.post('/api/chats', status_code=201)
    def create_chat(body: CreateChat, request: Request):
        check_origin(request)
        owner, store = chat_scope(request, body.store_id)
        return chats.create(owner, store)

    @app.get('/api/chats')
    def list_chats(request: Request, store_id: str):
        owner, store = chat_scope(request, store_id)
        return {'sessions': chats.list_sessions(owner, store)}

    @app.get('/api/chats/{session_id}')
    def get_chat(session_id: str, request: Request, store_id: str):
        owner, store = chat_scope(request, store_id)
        try:
            return chats.get(session_id, owner, store)
        except ChatNotFound:
            raise HTTPException(404, '会话不存在或无权访问') from None

    def store_engine(store_id, *, write=False):
        try:
            engine = (resolve_writer if write else resolve_engine)(store_id)
        except ValueError:
            raise HTTPException(403, '库存门店不匹配或数据库尚未初始化') from None
        if engine is None:
            raise HTTPException(503, '请先配置本店库存数据库和门店标识')
        return engine

    @app.post('/api/pharmacy/ocr_prefill')
    async def ocr_prefill(request: Request, image: UploadFile = File(...),
                          store_id: str = Form(..., min_length=1, max_length=100)):
        """Extract only; no writer engine or inventory transaction is opened."""
        try:
            check_origin(request)
            store_engine(store_id.strip())
            if image.size is not None and image.size > MAX_IMAGE_BYTES:
                raise HTTPException(413, '图片不能超过5MB')
            data = await image.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                raise HTTPException(413, '图片不能超过5MB')
            vision = (vision_factory or VisionGateway)()
            return await run_in_threadpool(extract_prefill, data, vision=vision)
        except VisionError as exc:
            if str(exc) == 'VISION_NOT_CONFIGURED':
                raise HTTPException(503, '尚未配置视觉模型，可先手动填写表单') from None
            raise HTTPException(502, '视觉模型请求失败，请核对模型配置或手动填写') from None
        except ValidationError:
            raise HTTPException(502, '模型未返回合格的结构化结果，请重拍或手动填写') from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        finally:
            await image.close()

    @app.post('/api/pharmacy/confirm_import', status_code=201)
    def confirm_import(body: ConfirmImport, request: Request):
        """Explicit employee confirmation creates inventory and details together."""
        check_origin(request)
        engine = store_engine(body.store_id, write=True)
        try:
            return import_confirmed(engine, body)
        except DuplicateInventory:
            raise HTTPException(409, '同名、品牌、规格和货架的商品已存在，本次未重复入库；请人工核对库存') from None
        except SQLAlchemyError:
            raise HTTPException(503, '数据库暂不可写，请稍后重试或核对权限；本次未完成入库') from None

    @app.post('/api/agent/stream', response_class=StreamingResponse)
    @app.post('/api/pharmacy/copilot/stream', response_class=StreamingResponse)
    def stream_copilot(body: CopilotRequest, request: Request):
        check_origin(request)
        if body.workspace['task'] == 'coaching':
            raise HTTPException(422, '当前卡片入口支持接待、找药和安全核查任务')
        # Clear current risk must still be displayed when the inventory service is down.
        emergency = bool(risk_matches(body.question, {'symptoms': body.workspace['customer'].get('symptoms', '')}))
        engine, data_mode, model = None, 'unconfigured', None
        if not emergency:
            try:
                engine = resolve_engine(body.workspace['store_id'])
            except ValueError:
                raise HTTPException(403, '库存门店不匹配或数据库尚未初始化') from None
            if engine is None:
                raise HTTPException(503, '请先配置本店库存数据库和门店标识')
            try:
                data_mode = 'mock' if inventory_is_mock(engine) else 'real'
            except SQLAlchemyError:
                raise HTTPException(503, '库存数据库暂不可用，请人工核对') from None
            model = new_model()
        if body.turn_id and not body.session_id:
            raise HTTPException(422, 'turn_id 必须关联 session_id')
        owner, store, turn_id = None, body.workspace['store_id'], body.turn_id or uuid4().hex
        if body.session_id:
            owner, store = chat_scope(request, store)
            try:
                history = chats.begin_turn(body.session_id, owner, store, turn_id, body.question, body.workspace['customer'])
                body = body.model_copy(update={'history': [HistoryMessage(**item) for item in history]})
            except ChatNotFound:
                raise HTTPException(404, '会话不存在或无权访问') from None
            except ChatBusy:
                raise HTTPException(409, '该请求已提交或上轮尚未结束，请重新打开会话查看') from None
            except sqlite3.Error:
                raise HTTPException(503, '聊天记录暂时无法保存，本次未发送') from None
        final_state = {}  # Per request only; not global, not sent to the browser.
        iterator = pharmacy_event_stream(body, engine, model=model, on_complete=final_state.update,
                                         data_mode=data_mode)

        def persisted_events():
            completed, failed, stages = False, False, []
            try:
                for frame in iterator:
                    event = json.loads(next(line[6:] for line in frame.decode('utf-8').splitlines()
                                            if line.startswith('data: ')))
                    if event['event'] == 'progress' and event['payload'].get('label'):
                        attempt = event['payload'].get('attempt', 0)
                        stages.append(event['payload']['label'] + (f' · 第{attempt}轮' if attempt else ''))
                    if event['event'] == 'done' and body.session_id:
                        if not final_state:
                            raise RuntimeError('Missing final state')
                        try:
                            chats.finish_turn(body.session_id, owner, store, turn_id, final_state, stages)
                        except (sqlite3.Error, ChatNotFound, ChatBusy):
                            failed = True
                            yield encode_sse(ui_event(event['run_id'], event['seq'], 'error', {
                                'code': 'CHAT_SAVE_FAILED', 'message': '回答未成功保存，请重新打开会话核对', 'retryable': False}))
                            return
                        completed = True
                    if event['event'] == 'error':
                        failed = True
                    yield frame
            finally:
                iterator.close()
                if body.session_id and not completed:
                    try:
                        chats.interrupt_turn(body.session_id, owner, store, turn_id, failed=failed)
                    except (sqlite3.Error, ChatNotFound):
                        pass

        frames = persisted_events() if body.session_id else iterator

        async def send_events():
            try:
                while not await request.is_disconnected():
                    frame = await run_in_threadpool(next, frames, None)
                    if frame is None:
                        break
                    yield frame
            finally:
                # Stop between node boundaries. Already running SQL/model calls cannot be force-killed.
                with anyio.CancelScope(shield=True):
                    await run_in_threadpool(frames.close)
                    if body.session_id:
                        try:
                            await run_in_threadpool(chats.interrupt_turn, body.session_id, owner, store, turn_id)
                        except (sqlite3.Error, ChatNotFound):
                            pass
                final_state.clear()

        return StreamingResponse(send_events(), media_type='text/event-stream', headers={
            'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no',
            'X-Inventory-Data-Mode': data_mode})

    @app.get('/')
    def index():
        if not (dist / 'index.html').is_file():
            return HTMLResponse('前端尚未构建。请在 web 目录执行 npm ci 和 npm run build。', status_code=503)
        return FileResponse(dist / 'index.html', headers={'Cache-Control': 'no-cache'})

    app.mount('/assets', StaticFiles(directory=dist / 'assets', check_dir=False), name='assets')
    return app


app = create_app()


def main():
    import uvicorn
    load_local_env()
    uvicorn.run('meditouch.api:app', host=os.getenv('HOST', '127.0.0.1'),
                port=int(os.getenv('PORT', '8765')))
