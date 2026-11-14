# 药店执行链路的依赖边界

当前药店入口为 `meditouch/api.py`，执行图为 `meditouch/pharmacy_graph.py`。

- `pharmacy_state.py` 定义独立的 `PharmacyState`，包含库存、Mock RAG、排除药品、安全核查和执行计数等字段，不包含旧证据图的消息、草稿与校验状态。
- `risk.py` 只依赖 Python 标准库，统一保存危险信号规则、否定与历史信息处理、风险来源和停止响应。此次迁移保留原规则及返回字段，不构成新增医学能力。
- 药店 API 直接引用公共风险模块；药店图直接引用自己的 State，不再经由旧患者咨询流程或旧 Agent 图加载它们。
- 旧 `agent_graph.AgentState` 继承 `PharmacyState` 以兼容已有调用；旧 `consultation_flow` 重新导出公共风险函数。依赖方向为旧模块引用公共模块，而非药店入口加载旧模块。
- `respiratory_guidance` 使用公共的咽痛识别表达式，避免出现两份不同版本。

这一步只调整依赖，不更改工具、路由、SSE 协议、库存数据或前端。

## 验证

```powershell
python -B -m unittest tests.test_pharmacy_dependencies -v
```

测试在新 Python 进程中禁止导入旧 Agent 图、咨询流程、患者上下文和证据服务，实际执行药店空库存查询及风险 SSE 响应，防止模块缓存掩盖传递依赖。完整业务行为继续由现有图、工具和 HTTP 测试覆盖。
