# 知衡 · 医疗证据工作台 v0.1

## 本机已配置的 DeepSeek 模型

2026-10-30 已配置用户指定的 `https://maas.qianwenaiapi.com/compatible-mode/v1` 与
`deepseek-v4.1-flash`。密钥通过 Windows 当前用户 DPAPI 加密存放于忽略的 data 目录，
仅启动时解密到子进程环境；不存入源码、文档或评测轨迹。

```powershell
cd D:\demo
.\start_with_model.ps1 -Action Check   # 两次小规模在线检查，只发送测试文本和虚构证据
.\start_with_model.ps1 -Action Server  # 启动启用模型的网页服务
```

直接 `python server.py` 仍只读取原有环境变量，不自动解密本地密钥。
加密文件仅适用于当前 Windows 用户；跨账号或迁移电脑需要重新配置，不复制认证状态。
本轮连接及合成证据引用格式检查已通过，不代表医学验证。
未停止或重启已有端口进程；若8765已占用，先确认现有服务，或设置 PORT 后启动。

Claw-Eval 的无密钥配置为 `integrations/claw/aliyun.yaml`。通过启动器运行命令时注入模型环境：

```powershell
.\start_with_model.ps1 -Action Claw -EvalArguments @('run','--help')
```

真实模型任务需按Claw CLI传入任务文件及上述配置。当前自定义离线runner不会因为配置密钥而自动变成模型评测；
官方任务资源与领域工具端点接入仍需单独完成。judge默认关闭，未自行扩大为批量付费调用。

## Claw Eval 自定义评测

已接入用户提供的Claw-Eval快照的任务、评分、轨迹组件，完成18题×3次本地规则评测。
详见[评测说明](eval_results/README.md)。不是官方300题或模型端到端评测。

## 高血压正文检索样本

已新增独立的离线清理、章节切分、原文定位与BM25检索试验，见
[样本说明](rag_data/hypertension/README.md)。运行 `python rag_pipeline.py build`、
`python rag_pipeline.py evaluate` 可复现。产物待审核，不自动进入正式问答；没有模型生成步骤。

## 可选 Craft 工作台接入

已新增只读 MCP 连接层，保留现有网页与规则流程。接入配置、演示步骤、验证和边界见
[Craft 接入说明](integrations/craft/README.md)。可运行 `python -m unittest -v` 验证全部12个测试；
独立环境的协议验证运行 `.\.venv-craft\Scripts\python.exe check_craft_mcp.py`。
当前完成协议级验证，尚未完成 Craft 客户端与真实模型端到端验证。

本地医疗 Agent 原型，Python 3.10+，无需第三方依赖。当前为规则编排 + 可选模型辅助，不依赖 LangChain/LangGraph。

## 启动

```powershell
cd D:\demo
python server.py
```

浏览器打开 http://127.0.0.1:8765 。服务仅绑定本机。停止：终端 Ctrl+C。

## 可选模型接入

支持 OpenAI-compatible Chat Completions。在启动服务的同一终端配置：

```powershell
$env:MED_AGENT_API_KEY = '你的密钥'
$env:MED_AGENT_BASE_URL = 'https://api.openai.com/v1'
$env:MED_AGENT_MODEL = '你账号可用的模型名称'
python server.py
```

未配置密钥时，真实执行本地检索、主张比较和证据摘要；不会模拟模型回答。配置后，问题和检索出的结构化主张会发送给所配置的模型服务，请仅使用获得授权的数据。密钥不发送到网页、不写入数据库。模型失败或引用 id/格式校验失败时回退；格式通过不保证医学正确性，模型说明保留为独立部分。

## 功能与边界

- 知识问答：本地词项重合检索，非向量/混合检索。
- 报告文本阅读：接收粘贴文本并检索资料；尚未实现 OCR、指标抽取、异常判定，不声称能完成医学报告解读。
- 主张比较：仅比较人工标注的相同 topic/population/condition，且演示与非演示证据隔离。不同措辞、单位换算、隐含条件可能导致漏检。
- 版本仲裁：仅在同一主张范围、显式 supersedes 且日期更新时排除旧版本。替代关系来自导入者，系统不独立核验其真实性。其他相反主张保留分歧，不多数投票。
- 不自动推断用户属于任何人群；回答呈现全部匹配范围。
- 内置 3 份虚构资料，只用于演示冲突；不是临床知识库。
- SQLite 保存资料、问题、回答及证据快照（data/agent.db）。无认证、无静态加密、无多用户隔离。仅供本机原型验证，不部署公网、不录入真实患者信息。
- 执行记录保留最近 30 条的展示；数据库不自动删除历史记录。

## 导入文档

网页“知识资料”导入 JSON 对象或对象数组，格式见 example-document.json。相同 id 更新文档。quote 必须逐字存在于 text；结构化主张须经过人工核验，来源必须填写真实机构、日期与原文定位信息。自由文本可检索，但未提供 claims 时不生成医学主张。

## 评测

```powershell
python -m unittest -v
```

网页可运行 4 项隔离的合成规则回归。命令行另覆盖日期不能独自裁决、引用伪造、演示隔离、无证据、输入注入和报告模式。测试不调用真实模型，不代表临床验证。

后续需要：经专业人员审核的真实知识库与版本治理、条件及单位归一化、混合检索与检索标注集、NLI 冲突检测、逐主张引用验证、独立医学评测集、工具授权与身份隔离。
