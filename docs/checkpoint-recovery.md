# 节点级恢复

有服务端会话的请求使用 `langgraph-checkpoint-sqlite==3.1.3`。会话库负责聊天历史；同目录的 `*.checkpoints.sqlite` 保存图的输入和节点状态，执行时采用同步检查点写入。没有会话的独立接口请求不保存检查点。

服务端以用户、门店、会话、轮次共同生成线程标识，每轮独立保存。前端不能传检查点ID、线程标识或状态值。恢复复用原问题与顾客信息，不插入第二条用户消息；同会话互斥，只有最后一条未完成请求可继续，每轮最多恢复3次。已完成请求不能重复恢复。

## 使用

失败或中断后，最后一条未完成消息显示“继续处理”。刷新后也可点击。普通服务重启后，旧进程的 processing 记录会显示中断。按钮提交原 session_id、turn_id 和 `resume: true` 到原 SSE 接口。

恢复首先读取检查点。普通健康解释继续待执行节点，已经完成的解释不重复生成。检查点尚未建立（例如语义理解时中断）时重新理解原问题。模型生成到一半时必须重新运行未完成节点，不从中途Token继续。

需要库存的任务会保留已理解的需求和已完成的健康解释，清除旧库存、缺货结果、禁忌结论及原排除集合。通过图状态更新指定 `resume_inventory_reset`，回到库存节点，再执行安全核查。这样既不重跑全部流程，也不将旧库存或旧禁忌当成当前结论。查询次数预算按恢复执行重新计数，但单轮恢复次数有总上限。

## 验证

```powershell
.\.venv-agent\Scripts\python.exe -m unittest tests.test_checkpoint_recovery -v
# 浏览器验收：独立终端启动合成故障夹具，再运行检查脚本
.\.venv-agent\Scripts\python.exe -m scripts.recovery_ui_fixture
.\.venv-agent\Scripts\python.exe -m scripts.check_recovery_ui
```

覆盖库存查完后安全节点异常、关闭并重新打开检查点、重建应用实例、修改库存与货架后恢复、修改禁忌后拦截、跨用户检查点隔离、同轮互斥、恢复上限及进程标识变化。

## 边界

- 适用于当前单进程、单机Demo。进程标识区分服务重启，不作为多Worker分布式锁；多实例部署需改用带租约的执行账本和相应持久化后端。
- 这是节点级续跑，不是SSE断线续传。恢复会开启新的SSE运行，不重放原网络事件。
- 图节点内已开始但未完成的模型或检索调用可能重跑；没有自动恢复到函数中间。
- 库存查询与安全核查重新执行，不代表已实现专业医学审核或售药授权。库存写操作仍在员工显式确认的独立接口中，不在恢复图里执行。
- 检查点含会话和业务状态，存于Git忽略的本地data目录，应按聊天数据保护；当前没有自动过期清理。

实现参考：[LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)、[SQLite Checkpointer](https://reference.langchain.com/python/langgraph.checkpoint.sqlite/SqliteSaver)。
