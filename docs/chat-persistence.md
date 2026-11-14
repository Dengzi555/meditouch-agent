# 本地会话与消息持久化

默认数据库为 `data/pharmacy_chat.db`，独立于库存库。可用服务端环境变量 `MED_AGENT_CHAT_DB` 指定路径。
第一次使用会话接口时自动创建表，不导入、覆盖或删除库存数据。

- `pharmacy_chat_sessions`：会话 ID、owner_id、store_id、标题和时间。
- `pharmacy_chat_messages`：会话 ID、turn_id、角色、正文、卡片及必要业务状态、完成状态和时间。
- 不保存 API 密钥、系统 Prompt 或模型内部推理。目录 data/ 已被 Git 忽略。

## API

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/chats` | `{store_id}` 创建会话 |
| GET | `/api/chats?store_id=...` | 加载当前身份与门店最近200个会话 |
| GET | `/api/chats/{id}?store_id=...` | 加载该会话的消息和最终卡片 |
| POST | `/api/agent/stream` | 传 session_id、turn_id、question、workspace，执行并保存一轮 |

发送前用事务保存用户问题与待完成回答占位；`turn_id` 防重复，同一会话不并发执行两轮。
最终回答写库成功后才发送 SSE done；失败时返回 error，不伪装为已经保存。
中断、失败的回答不恢复为完成，不展示其中的候选卡片。服务异常退出留下的 processing 占位在5分钟后可按中断恢复。
刷新时旧请求的退出和保存可能稍有延迟；仍在处理的会话会提示稍后重新打开。

前端在 URL 中使用 `?chat=会话ID` 恢复当前对话；普通根地址展示已有会话列表，可手动选择。
会话请求的多轮历史由数据库读取，不接受客户端 history 覆盖已保存的会话。
请求不带 session_id 时保留原有无持久化接口行为，兼容旧调用方。

## 身份与范围

当前本地 Demo 的身份由服务端固定为 `local-demo`，未实现登录认证。
所有查询和写入都同时约束 owner_id 与 store_id。客户端不能提交 owner_id 选择身份。
后续接账号时，将 create_app 的 identity_resolver 替换为服务端验证过的登录身份，并增加真实门店成员权限验证。
字段及数据库过滤是账号隔离的基础，不能当成已经完成多用户鉴权。

本次改动前只在浏览器内存中的记录不会自动迁移；已被刷新清除的记录无法恢复。
