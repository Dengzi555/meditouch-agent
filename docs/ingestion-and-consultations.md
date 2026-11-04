# 新资料入库、咨询状态和追问续接

实现日期：2026-11-02。范围为资料处理与单次咨询状态；不包含自动诊断、处方、临床分诊或医学审核发布。

## 新资料入库

`reference_ingestion.py` 处理 `knowledge_sources/respiratory_reference_20261102/manifest.json` 中成功下载的5份原件，跳过并保留下载失败记录。网页仅提取 `.TRS_UEDITOR` 正文，去除导航、脚本、样式和页脚；短篇科普整篇保留，避免拆散例外条件。18页流感方案按一级章节切分，跨页章节保留全部定位跨度与原文，未按字数硬截断。

本批得到17单元：PDF引言及12章共13个、网页科普4个；合并旧语料后真实索引共30个。治疗章约4514字符，作为完整章节保留并加长章节标记；其表格、数值和剂量需人工对照PDF核对。没有自动抽取临床claims。

```powershell
python -B reference_ingestion.py
```

输出至 `rag_data/respiratory_reference/`：

- `chunks.json`：标准证据单元，含出处、日期、原文、PDF页码/网页定位、抽取坐标及质量标记。
- `review_queue.json`：逐单元待审核项目及核对事项。
- `REVIEW_QUEUE.md`：便于人工阅读的审核清单。

全部固定pending且generation_allowed=false，生成步骤不能自授审核。读取时校验原件和PDF文字抽取的哈希，从来源重新构造分块并与索引比较；修改原文、索引、引用或审批字段后会失败关闭，需要重新构建。指纹是内容一致性校验，不是医学审核、可信签名或防止有本机权限者同时篡改所有文件的机制。

默认 `EvidenceService` 已合并新索引，HTTP、MCP统一入口与单Agent均可检索。自定义PDF语料不会自动混入新包；测试可显式注入reference_paths。新机器需先下载资料再构建，原件和派生内容仍被Git忽略，不随源码分发。

## 会话与追问

`consultation.py` 使用现有本机SQLite库新增 `consultations` 表，保持已有docs/runs。每次咨询包含随机session_id、原问题、固定语料、人群及症状等结构化字段、来源声明、更新时间、revision和运行状态。不会把“不清楚”转换为“无异常”，不会自行从自然语言猜出未提供的体温或持续时间。

`patient_context.py` 定义字段与按问题选择的追问规则：症状咨询收集相关人群、症状和持续时间；涉及发热时收集体温及测量方式；涉及用药时可补充药名规格、成分和既往用药情况；肾功能沿用钾盐相关条件。伴随症状等字段可保存。规则用于信息收集，不用于判断严重程度、药物安全或诊断。

缺少条件时由宿主直接进入等待状态，不耗费模型调用；补充后只追问仍缺的字段，满足当前信息收集条件再把原问题与已知声明交给现有LangGraph。这里恢复的是咨询任务和结构化信息，不是从某次模型执行到一半的LangGraph节点恢复，也没有自动建设跨会话健康画像。

API：

```json
POST /api/agent
{"question":"我发烧了怎么办？","corpus":"real"}
```

返回session_id、revision、missing_context、context和原有结果字段。继续咨询：

```json
POST /api/agent
{"session_id":"返回的32位ID","revision":1,"known_context":{"population":"成人","duration":"两天"}}
```

继续请求可省略question/corpus，不能换问题或换语料；如提供必须与原值一致。显式提交空字符串会删除旧回答并重新追问，省略字段则保留。GET `/api/session/{session_id}` 可恢复已保存结果和结构化信息。不合法字段拒绝写入，过期revision和并发重复提交返回409；执行失败释放租约，进程中断后租约至多180秒过期，可在同revision重试。

前端提供“开始新咨询”，补充字段后点击继续；刷新通过浏览器sessionStorage中的会话ID恢复，浏览器不保存患者字段文本。新咨询不继承旧咨询的字段；按钮不会删除历史记录。原问题在会话期间只读，换题应开始新咨询。

保存范围仍为本机原型：咨询信息写入本机SQLite且可恢复，不具备账号鉴权、跨用户访问控制、静态加密或正式保留期限策略，不应用真实患者隐私做测试。当前实现没有独立的临床急症分诊机制。

## 验证

新增资料测试覆盖正文去噪与限制条件保留、原件/索引篡改阻断、PDF跨页坐标、审核清单对应。会话测试覆盖部分回答、新对象恢复、未知值保留、清空后重问、新会话隔离、旧版本提交、并发租约与失败恢复。HTTP集成覆盖多轮补充后继续原问题并进入工具检索。

```powershell
.\.venv-agent\Scripts\python.exe -B -m unittest -q
.\.venv-eval\Scripts\python.exe -B -m unittest -q
node --check web/app.js
```

测试通过是工程证据，不代表临床验收。源码、HTTP会话与前端按当前字段同步后再启动服务。
