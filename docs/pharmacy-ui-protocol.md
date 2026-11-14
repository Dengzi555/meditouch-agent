# 药店 Copilot 卡片与 SSE 协议

版本：`pharmacy.ui.v1`。卡片由宿主程序生成，前端不解析 Markdown、不根据文案猜测安全性。展示执行节点、库存事实和拦截结果，不展示隐藏推理、System Prompt、原始模型消息或完整 trace。

## 当前范围

- 真实 B 端 JSON 返回已增加 `ui_cards` 和 `staff_script`，与原文本 `answer` 并存。
- `meditouch/pharmacy_ui.py` 提供卡片转换、SSE 编码及真实 LangGraph updates 适配器。
- `examples/pharmacy_ui/server.py` 是独立的**合成数据 SSE 演示服务**；不会查询真实库存。
- 默认网页已替换为 `web/src/App.tsx` 和 React 卡片；`/api/agent/stream` 现为真实 SSE，原 NDJSON 实现已删除。
- `meditouch/api.py` 的 FastAPI 路由接收 question/workspace，校验输入及配置的门店，再遍历真实 LangGraph updates。最终 State 由请求内回调捕获，不向浏览器泄露 messages/trace。
- 当前仍是单店 demo，没有员工权限系统或生产售药授权；后端不会自动放行。

## 事件信封

```json
{
  "schema_version": "pharmacy.ui.v1",
  "run_id": "run-01",
  "seq": 3,
  "event": "cards.snapshot",
  "emitted_at": "2026-11-13T08:00:00+00:00",
  "payload": {
    "safety_status": "unknown",
    "cards": []
  }
}
```

| 事件 | payload | 前端动作 |
| --- | --- | --- |
| `progress` | `stage`、`phase`、`attempt`、`label` | 显示实际完成的节点和轮次 |
| `cards.snapshot` | `safety_status`、完整 `cards` 数组 | 整体替换旧卡片；空数组也必须清空 |
| `done` | `status="completed"`、`sale_authorized=false` | 正常结束，不代表可售药 |
| `error` | `code`、安全处理过的 `message`、`retryable` | 清除候选，显示人工核对提示 |

`seq` 在同一 run 内递增；重复或旧序号忽略，不同 run 不混用。当前不支持断点续传、历史事件重放或自动重试 POST；断线提示未完成，人工重新发起。序号不等于已经实现业务幂等或取消所有后端调用。

## 卡片类型

| type | 关键字段 | 呈现 |
| --- | --- | --- |
| `intercept_warning` | `items=[{name,reason}]` | 红色拦截警告；排除名单非空时显示 |
| `drug_candidate` | `drug={inventory_id,name,shelf_location,stock,is_rx,specification}`、`safety_status`、`availability` | 明确核查通过且有货才绿色；未知黄色待核；缺货灰色 |
| `missing_information` | `message`、`reason` | 黄色，包含“请人工核对病史” |
| `staff_script` | `text` | 灰色话术框，仅展示宿主已确认话术 |
| `notice` | `message` | 查询异常、无匹配或仍不安全的状态提示 |

所有药品卡固定 `requires_pharmacist_review=true`、`sale_authorized=false`。处方药同时显示明确文字警示。当前禁忌工具只返回 `false` 或 `unknown`，真实链路不会因为未命中而显示绿色；协议保留未来核验器的通过分支。

绿色不是营销推荐，也不是售药授权。重试前拦截的药不再作为候选展示，原因独立保留。新一轮库存返回时，在本轮安全节点完成之前强制呈现 unknown，不能沿用上一轮绿色状态。

## Python：模拟 generator 返回卡片

```python
from meditouch.pharmacy_ui import build_ui_cards, encode_sse, ui_event

state = {
    "user_query": "查演示药",
    "excluded_drugs": ["演示药甲"],
    "excluded_reasons": {"演示药甲": "演示标签禁用"},
    "inventory_results": [{
        "inventory_id": 2, "name": "演示药乙", "shelf_location": "3排2号",
        "stock": 5, "is_rx": False
    }],
    "safety_status": "unknown",
    "safety_reason": "缺乏禁忌数据，建议咨询药师",
    "safety_checks": []
}
cards = build_ui_cards(state, staff_script="我先帮您核对病史，再请药师确认。")
event = ui_event("run-01", 1, "cards.snapshot", {
    "safety_status": state["safety_status"], "cards": cards
})
# generator 可 return {"ui_cards": cards}；HTTP 层编码后 write/flush。
frame_bytes = encode_sse(event)
```

SSE wire format（每个事件以空行结束）：

```text
id: run-01:1
event: cards.snapshot
data: {"schema_version":"pharmacy.ui.v1", ...}

```

响应头使用 `Content-Type: text/event-stream; charset=utf-8`、`Cache-Control: no-cache, no-transform`。每帧写完 flush；代理需关闭响应缓冲。长耗时节点可加 `: keep-alive\n\n` 心跳，但不能用定时假进度代替真实执行状态。

## Python：接真实 LangGraph updates

```python
from uuid import uuid4
from meditouch.pharmacy_graph import build_pharmacy_graph
from meditouch.pharmacy_ui import graph_ui_events, encode_sse

# engine 和 initial_state 由已校验的服务端逻辑准备，不能来自模型数据库参数。
compiled = build_pharmacy_graph(engine, model=model)
for event in graph_ui_events(compiled, initial_state, uuid4().hex):
    response_writer.write(encode_sse(event))
    response_writer.flush()
```

适配器从 `compiled.stream(..., stream_mode="updates")` 读取节点真实完成结果。inventory、安全、生成和人工交接节点各自输出进度和卡片快照。中间节点不发送店员话术；生成节点完成后才发送。异常仅发清理过的错误事件，不泄露数据库路径。断开连接后应停止写入；这不等于可立即取消已经运行的 SQL 或模型请求。

## React / Next.js 示例

完整组件：`examples/pharmacy_ui/CopilotCards.tsx`，样式：同目录 `CopilotCards.module.css`。复制到已有 Next.js App Router 客户端组件目录，再在页面渲染 `<CopilotCards />`。

正式页面的核心 Hook 为 `web/src/useCopilotStream.ts`，原生读取器为 `web/src/sse.ts`，卡片组件为 `web/src/CopilotCards.tsx`。组件使用 POST `fetch` 读取 SSE，处理 UTF-8 分片、事件跨网络块、CRLF、重复序号、任务隔离、快照替换和中止请求。使用 React 文本节点，不使用 `dangerouslySetInnerHTML`。标准 `EventSource` 不提供该例所需的 JSON POST 请求体。

从仓库根目录启动合成演示：

```powershell
.\.venv-agent\Scripts\python.exe -m examples.pharmacy_ui.server
```

默认组件请求 `http://127.0.0.1:8766/events`。演示服务仅允许本地 Next.js 3000 端口的跨域请求。示例不自带 Next.js 项目，不会启动第二套正式前端，也没有接入真实库存或模型调用。

正式页面使用相对地址 `/api/agent/stream`，不再请求示例服务。生产构建由 FastAPI 同源托管；开发可运行 `npm --prefix web run dev`，Vite 将 `/api` 代理到 8765。

## FastAPI 路由与验证

完整路由：`meditouch/api.py`。`StreamingResponse` 返回 `text/event-stream`，逐节点写入标准 SSE 帧。同步 SQL/图步骤在线程池运行，不阻塞 ASGI 事件循环；断线后停止继续拉取节点，已运行的模型/SQL 不能强制终止。最终 State 只保留在请求内，响应结束后释放。

配置错误或参数错误在发送 SSE 头之前返回 HTTP JSON 错误；流中异常使用 `event: error`，不会伪装 `done`。前端遇到错误或缺失 done 会清空候选，禁止自动重试 POST。

验证：Python `tests.test_api_sse` 覆盖真实图与门店查询；`npm --prefix web test` 覆盖逐字节 UTF-8、CRLF、事件拆包及非法卡片；`scripts/check_pharmacy_ui.py` 用隔离库存和脚本模型验证浏览器操作。安装可选 Playwright 并准备 Edge 后，在终端一运行 `python -m scripts.pharmacy_ui_fixture`，在终端二运行 `python -m scripts.check_pharmacy_ui`。该隔离服务仅监听8767，不连接真实库存，不作为正式启动入口。

测试：`python -m unittest tests.test_pharmacy_ui -v`。完整示例事件见 `examples/pharmacy_ui/sample-events.json`。

参考：[MDN SSE 格式](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)、[React Effect 清理](https://react.dev/reference/react/useEffect)。
