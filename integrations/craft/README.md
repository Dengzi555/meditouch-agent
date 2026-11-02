# Craft 医疗证据连接层

## 当前交付

保留 Python 医疗证据服务，通过官方 MCP Python SDK 暴露 stdio 工具。
Craft 负责外层会话和工具选择，医疗服务负责确定性的证据摘要、版本排除及分歧保留。
现有网页启动方式不变，不需要先运行 HTTP 服务才能使用 MCP。

```text
Craft Session → stdio MCP → EvidenceTools → 原有检索与仲裁函数
                                  ↓
                         SQLite mode=ro 原文与主张
```

新增工具：

| 工具 | 输入 | 结果 |
| --- | --- | --- |
| search_evidence | question | 最多8篇匹配资料、完整原文与主张、限制说明 |
| analyze_evidence | question | 规则摘要、状态、证据快照、版本排除与轨迹 |
| get_evidence_source | source_id | 按ID获取完整资料，不接受路径或URL |

连接层不调用第二个模型、不写网页运行记录、不导入采集目录，也不自动发布资料。
数据库连接以只读模式打开且调用结束后关闭；缺失数据库时返回错误而不创建文件。
当前工具结果中的 id 是该次调用标识，不表示已持久化。

## 环境准备

本机已在 `D:\demo\.venv-craft` 安装 MCP SDK 1.30.0。重新准备环境时执行：

```powershell
cd D:\demo
python -m venv .venv-craft
.\.venv-craft\Scripts\python.exe -m pip install -r requirements-craft.txt
```

只需连接层安装额外依赖；`python server.py` 仍使用标准库。
若无 data/agent.db，先按主 README 启动网页服务完成初始化，再停止或保留该服务均可。

## 在 Craft 中接入

本目录的 `medical-evidence/config.json` 和 `guide.md` 是准备好的 Source 文件。
源码确认 Sources 目录结构为 `{workspaceRoot}/sources/{sourceSlug}/`。
将 `medical-evidence` 整个目录放入所选 Craft 工作区的 `sources` 下，再刷新 Sources 并测试连接。
本轮没有写入用户 Craft 工作区、认证文件或外部项目，也没有将 connectionStatus 伪标为 connected。

配置使用本机绝对路径：

```json
{
  "transport": "stdio",
  "command": "D:\\demo\\.venv-craft\\Scripts\\python.exe",
  "args": ["D:\\demo\\medical_mcp.py"],
  "env": {"PYTHONIOENCODING": "utf-8"}
}
```

移动目录时需同步修改 command 和 args。该块应放在 Craft Source 配置的 mcp 字段中，
不要将它当作独立 HTTP 地址。配置不包含模型密钥。Craft 自身的模型连接另行配置。

## 演示步骤

1. 在 Craft 中启用“知衡医疗证据”Source。
2. 提问：“请调用 analyze_evidence 查询演示措施X存在什么分歧，保留原文和人群条件。”
3. 检查结果为 unresolved，demo-old 被排除，demo-new 与 demo-other 两方观点保留。
4. 调用 get_evidence_source，输入 demo-new，核对原文。
5. 提问无关字符串 zzzzunknown，应返回 insufficient；外层模型应保留证据不足结论。

上述脚本基于虚构种子资料。若实际数据库已变更，以现有资料为准；不要重置数据库来强行匹配演示。
协议冒烟测试使用隔离临时库，不依赖实际库内容。

## 验证与边界

```powershell
python -m unittest -v
.\.venv-craft\Scripts\python.exe check_craft_mcp.py
```

2026-10-30 验证结果：12个单元测试通过；官方 SDK ClientSession 通过真实 stdio 子进程完成初始化、
工具发现、3个工具调用、冲突与替代、证据不足、错误参数与未知工具、数据库字节不变检查。
这不等于 Craft 客户端已连接，也不等于真实模型遵循规则；本轮没有运行 Craft 应用或模型端到端测试。

源码核对位置（用户提供的本地 Craft 仓库）：

- packages/shared/src/sources/types.ts：FolderSourceConfig 与 stdio 字段。
- packages/shared/src/config/validators.ts：McpSourceConfigSchema 的 stdio 配置检查。
- packages/shared/src/sources/server-builder.ts：buildMcpServer 的 stdio 分支。
- packages/shared/src/sources/storage.ts：工作区 Sources 存储目录。

## 下一阶段

1. 对7份采集资料完成正文清理、章节切分与原文定位，并建立检索块数据结构。
2. 形成候选主张提取契约；未知条件不补全，引用校验与医学审核分开。
3. 建立待审核与发布门禁，正式问答只检索已发布资料。
4. 补查关联版本与同范围反证，评估当前 Top8 截断造成的遗漏。
5. 在 Craft 内验证工具调用轨迹及模型是否违背仲裁，再设计输出校验。

注意：现有库没有审核发布门禁，工具结果均明确提示此限制。source guide 是提示指导，
并不能强制限制外层 Craft 模型。工具返回的资料会进入 Craft 所选模型上下文，使用前确认数据授权。

## 项目与面试责任边界

| 模块 | 来源 | 状态 |
| --- | --- | --- |
| 会话、模型执行、Source加载与工具选择 | Craft 开源底座 | 本地源码核对，未运行客户端验收 |
| 检索、规则仲裁、证据摘要 | 当前医疗原型 | 已有能力，本轮回归通过 |
| 只读领域工具、MCP适配、Source配置、协议测试 | 本轮新增 | 实现并通过协议级验证 |
| 医学主张AI提取、人工审核、审核发布门禁 | 领域扩展 | 规划，尚未实现 |
| 生产部署、医学准确率或业务收益 | 无相关证据 | 不作已完成声明 |

官方协议与 SDK 参考：
- https://modelcontextprotocol.io/specification/2024-11-05/basic/transports
- https://github.com/modelcontextprotocol/python-sdk/tree/v1.x
