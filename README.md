# 医点通 · MediTouch

面向实体药店店员与实习生的 Agent 工作台，覆盖健康问答、顾客接待、本店找药、药盒识别和库存管理。

系统结合会话历史理解需求，检索健康资料，查询药品货架与库存，并筛查已录入的禁忌。遇到危险表现时切换到应急处置与现场记录，帮助店员整理交接信息。

[![CI](https://github.com/Dengzi555/meditouch-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Dengzi555/meditouch-agent/actions/workflows/ci.yml)

## 功能

| 功能 | 说明 |
| --- | --- |
| 健康问答 | 查询疾病、症状及药品一般知识，按主题组织回答，突出重点并附参考来源 |
| 多轮接待 | 结合历史补全省略问句，识别症状、药名、人群及否认信息，通过点选追问继续补充情况 |
| 本店找药 | 按通用名、品牌或症状线索查询商品，展示货架、库存、规格与处方标记 |
| 禁忌筛查 | 对库存候选逐药检查，记录禁忌命中原因，携带排除清单重新查找候选 |
| 应急处置 | 优先显示呼救与条件式急救提示，记录现场情况并生成可复制的交接摘要 |
| 拍照入库 | 一张图片识别多种药品，分条预填表单，确认药品信息、货架与数量后入库 |
| 库存管理 | 搜索和编辑商品，登记销售、补货、退货、盘点；删除前确认，保留历史流水 |
| 会话与恢复 | SQLite 保存聊天和图检查点，中断后继续处理；恢复库存任务时重新核验库存与禁忌 |

## 界面

**本店找药与禁忌拦截**

![本店找药与禁忌拦截](docs/images/pharmacy-copilot-cards.png)

**健康问答与点选追问**

![健康问答与点选追问](docs/images/pharmacy-health-answer.png)

**库存管理与删除确认**

![库存管理](docs/images/pharmacy-inventory-management.png)
![删除药品确认](docs/images/pharmacy-inventory-delete.png)

<details><summary>拍照入库、重复提示与库存流水</summary>

![多药盒识别与确认入库](docs/images/pharmacy-inventory-import-multi.png)
![已有商品提示](docs/images/pharmacy-duplicate-import.png)
![销售出库确认](docs/images/pharmacy-stock-movement.png)
![库存变动记录](docs/images/pharmacy-stock-ledger.png)

</details>

<details><summary>应急处置与现场交接</summary>

![应急处置提示](docs/images/pharmacy-emergency-guidance.png)
![现场记录与交接摘要](docs/images/pharmacy-emergency-handover.png)

</details>

<details><summary>首页与模型设置</summary>

![首页](docs/images/home.png)
![模型设置](docs/images/api-settings.png)

</details>

[界面示例与截图复现](docs/interface-examples.md)

## 工作流程

```mermaid
flowchart TD
    A[店员提问与会话历史] --> B[语义理解与危险信号检查]
    B --> C{任务路由}
    C -->|危险情况| D[应急提示与现场交接记录]
    C -->|健康问答| E[公开知识 → 本地资料 → 模型知识]
    E --> F[组织健康解释并校验引用]
    F -->|同时查药| G
    C -->|找药或核安全| G[批量查询本店商品]
    G -->|需要补充线索| H[检索药品候选]
    H --> G
    G -->|有候选| I[逐药禁忌筛查]
    I -->|禁忌或缺货，预算允许| H
    I -->|完成或达到查询上限| J[商品卡片与接待话术]
    C -->|通用交流| K[生成回复]
    D --> L[SSE 推送卡片]
    F -->|健康解释| L
    J --> L
    K --> L
    L --> M[React 展示与会话保存]
```

### Agent 编排

LangGraph 组织健康问答、库存查询、禁忌筛查、知识补查和应急处置节点。语义入口结合会话生成独立查询，保留肯定、否认和未知信息。候选查询最多执行三轮，知识补查最多两轮，排除原因随状态保留。

健康问答按公开知识、本地资料、模型知识组织来源，生成结构化正文并校验引用。模型文本增量经 SSE 推送到前端，卡片逐帧呈现；应急提示即时完整显示。

SQLite Checkpointer 保存节点状态。恢复任务时继续原会话、原轮次，库存分支重新执行查询和筛查。[恢复机制](docs/checkpoint-recovery.md)

### 库存事务

库存、货架和商品属性由 SQLModel 查询提供。出入库使用事务、版本校验和请求幂等控制，库存变化与流水同时保存。同药名、品牌入库时提示已有记录，支持跳转编辑或确认新建。删除操作保存删除标记与快照，历史流水持续可查。[库存设计](docs/inventory-management.md)

## 技术栈

| 层次 | 技术 |
| --- | --- |
| Agent 编排 | Python、LangGraph、LangChain Tool Calling |
| 知识检索 | BM25、BGE 向量检索、RRF 融合、可选重排 |
| 数据与恢复 | SQLModel、SQLite、SQLite Checkpointer |
| 服务接口 | FastAPI、SSE、兼容 Chat Completions 的模型接口 |
| 前端 | React、TypeScript、Vite |
| 验证 | unittest、Vitest、Playwright、GitHub Actions |

## 快速启动

环境要求：Python 3.12+、Node.js 24。

```powershell
python -m venv .venv-agent
.\.venv-agent\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix web ci
npm --prefix web run build
```

加载演示商品并启动：

```powershell
.\.venv-agent\Scripts\python.exe seed_data.py
$env:MED_AGENT_INVENTORY_DB = (Join-Path (Get-Location) 'data/pharmacy_inventory_mock.db')
$env:MED_AGENT_INVENTORY_STORE_ID = 'local-store'
$env:MED_AGENT_API_KEY = '<模型密钥>'
$env:MED_AGENT_BASE_URL = '<兼容 Chat Completions 的接口地址>'
$env:MED_AGENT_MODEL = '<模型名称>'
.\.venv-agent\Scripts\python.exe server.py
```

打开 [http://127.0.0.1:8765](http://127.0.0.1:8765/)。在“本地门店 → API 配置”中设置文本模型与独立视觉模型，保存后从下一次请求生效。公开知识与搜索接口由部署者在服务端配置。[配置说明](docs/api-settings.md)

Windows 完成配置后，可使用启动脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_local.ps1
```

接入门店商品时，设置对应的 `MED_AGENT_INVENTORY_DB` 和 `MED_AGENT_INVENTORY_STORE_ID`，通过入库页面录入商品，再在库存管理中维护资料与计数单位。[商品录入](docs/pharmacy-image-import.md)

前端开发：`npm --prefix web run dev`，5173端口代理后端8765端口。

## API 与目录

`POST /api/agent/stream` 接收问题和门店上下文：

```json
{
  "question": "查找布洛芬缓释胶囊",
  "workspace": {
    "store_id": "local-store",
    "task": "drug_lookup",
    "customer": { "customer_tags": ["成人"] }
  }
}
```

响应采用 `text/event-stream`，包含 `progress`、`cards.snapshot`、`done` 和 `error` 事件。[卡片协议](docs/pharmacy-ui-protocol.md)

| 模块 | 路径 |
| --- | --- |
| API 与静态页面 | `meditouch/api.py` |
| 图编排与 State | `meditouch/pharmacy_graph.py`、`pharmacy_state.py` |
| 语义理解与查询补全 | `meditouch/pharmacy_semantics.py` |
| 健康知识与引用校验 | `meditouch/health_knowledge.py`、`pharmacy_health.py` |
| 库存模型与工具 | `meditouch/inventory_models.py`、`inventory_tools.py` |
| 库存管理与入库 | `meditouch/inventory_management.py`、`inventory_import.py` |
| 应急处置与交接 | `meditouch/emergency_support.py` |
| 会话与检查点 | `meditouch/chat_storage.py`、`checkpoints.py` |
| 前端页面与卡片 | `web/src/` |
| 自动化测试 | `tests/` |

## 测试与评测

```powershell
.\.venv-agent\Scripts\python.exe -m unittest discover -s tests -t .
npm --prefix web test
npm --prefix web run build
```

自动化测试覆盖路由、引用校验、流式协议、库存事务、删除确认、并发冲突、重复入库、节点恢复和应急记录。后端357项通过、4项跳过；前端17项通过。

| 评测集 | 用途 | 报告 |
| --- | --- | --- |
| 100题公开资料段落检索 | 20个主题，对比 BM25、混合检索和重排 | [检索评测](docs/eval-report.md) |
| 200题合成检索夹具 | 检索管线回归 | [回归评测](docs/eval-report.md) |
| 30条药店工作流场景 | 找药、禁忌、联合用药、缺货和急症路由 | [工作流评测](docs/evaluation/pharmacy-agentic-rag-30.md) |

## 文档

[药店工作流](docs/pharmacy-copilot.md) · [健康问答](docs/pharmacy-health-qa.md) · [库存管理](docs/inventory-management.md) · [应急处置](docs/emergency-support.md) · [配置与使用范围](docs/project-scope.md)

## 许可证

代码采用 MIT License。药品资料、外部内容及品牌素材的权利归各自权利人。
