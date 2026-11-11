# MCP 接入

医点通通过官方 MCP Python SDK 提供 stdio 工具，可连接支持本地 MCP 服务的客户端。客户端负责会话与模型调用，医点通负责资料读取、证据检索和结构化主张仲裁。

## 安装

在项目虚拟环境中安装可选依赖。

```sh
python -m pip install -r requirements-craft.txt
```

默认读取项目下的 `data/agent.db`。首次准备数据库可先运行一次 `python server.py`，随后停止网页服务。MCP 使用只读数据库连接，已有数据库必须可访问。

## 客户端配置

将下列路径替换为实际虚拟环境和项目的绝对路径，再写入客户端的 MCP 配置。

```json
{
  "mcpServers": {
    "meditouch": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/meditouch-agent/medical_mcp.py"]
    }
  }
}
```

Windows 的 `command` 使用虚拟环境中的 `Scripts/python.exe`。可在 `args` 中追加 `--db` 和数据库绝对路径。Craft Source 格式见同目录下的 [配置示例](medical-evidence/config.json)，其中本地路径需按环境替换。

## 工具

| 工具 | 输入 | 功能 |
| --- | --- | --- |
| `search_evidence` | `question` | 读取本地数据库匹配资料与主张 |
| `analyze_evidence` | `question` | 检索与仲裁，返回引用、替代关系和轨迹 |
| `get_evidence_source` | `source_id` | 按资料 ID 读取来源 |
| `search_materials` | `question, k, corpus` | 通过统一证据服务检索，返回原文、来源与页码 |
| `read_material` | `evidence_id, corpus` | 按证据 ID 核对资料 |

工具不接受任意路径或 URL 作为读取目标。数据库连接按只读方式打开，缺失数据库会返回错误。统一证据服务可读取本地已准备的资料索引。

## 验证

```sh
python check_craft_mcp.py
python -m unittest
```

协议验证涵盖初始化、工具发现、正常调用、证据冲突和错误参数。客户端所用模型应保留工具中的限制条件和未解决分歧。工具协议与字段校验独立于医学准确性评估。

## 参考

- [MCP 传输协议](https://modelcontextprotocol.io/specification/2024-11-05/basic/transports)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
