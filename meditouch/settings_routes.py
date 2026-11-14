"""Local-only settings routes; never expose saved secrets to the browser."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from meditouch.settings import SettingsStore

LOOPBACK = {'127.0.0.1', '::1', 'localhost'}


def require_local(request):
    # Check Host as well as the socket peer: a local tunnel must not expose settings.
    if (not request.client or request.client.host not in LOOPBACK
            or request.url.hostname not in LOOPBACK):
        raise HTTPException(403, 'API 配置仅允许从本机地址访问')
    origin = request.headers.get('origin')
    allowed = {str(request.base_url).rstrip('/'), 'http://localhost:5173', 'http://127.0.0.1:5173'}
    if origin and origin not in allowed:
        raise HTTPException(403, '拒绝跨来源配置请求')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, '拒绝跨站配置请求')
    # Reject proxy context, including tunnels terminating on a loopback socket.
    if any(name in request.headers for name in ('forwarded', 'x-forwarded-host', 'cf-connecting-ip')):
        raise HTTPException(403, '请直接使用本机地址配置 API')


def settings_router(store=None):
    store = store or SettingsStore()
    router = APIRouter(prefix='/api/settings')

    @router.get('')
    def read(request: Request):
        require_local(request)
        return JSONResponse(store.snapshot(), headers={'Cache-Control': 'no-store'})

    @router.post('')
    async def save(request: Request):
        require_local(request)
        if not request.headers.get('content-type', '').startswith('application/json'):
            raise HTTPException(415, '请使用 JSON 提交配置')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                raise HTTPException(413, '配置请求过大')
        import json
        try:
            data = json.loads(body)
            if not isinstance(data, dict) or set(data) != {'values'}:
                raise ValueError('配置请求格式不正确')
            result = store.save(data['values'])
        except ValueError:
            # Never echo validation input, provider keys, or local file contents.
            raise HTTPException(422, '配置无效，请检查地址格式、开关或文本长度；未保存') from None
        except OSError:
            raise HTTPException(503, '本地 .env 写入失败，请检查目录权限；未更新运行配置') from None
        return JSONResponse({**result, 'saved': True, 'effective': 'next_request'},
                            headers={'Cache-Control': 'no-store'})

    return router
