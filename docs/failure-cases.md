# 可复现的失败案例

以下轨迹由自动化测试实际执行，使用工程夹具。它们不包含用户病情，也不用于估算临床风险。

| 场景 | 轨迹与结果 | 处理 |
| --- | --- | --- |
| 证据范围不匹配 | `search_evidence → evidence_applicability: population_not_established → insufficient` | 引用前用原始问题与用户已声明的条件匹配证据策略。儿童或未知人群都不能满足成人限定。 |
| 模型引用了不存在的 ID | `validate: UNKNOWN_CITATION → 修正一次 → validation_failed` | ID 必须出现在当次检索结果中。模型连续返回伪造 ID 时，宿主丢弃草稿。 |
| 模型重复成功的工具请求 | `search_evidence → REPEATED_TOOL_CALL → budget_exceeded` | 工具名和参数用于识别重复调用。成功过的相同请求不会再次执行。 |

```sh
python -m unittest -v test_evidence_applicability test_agent_graph.GraphTests.test_forged_answer_gets_one_repair_then_blocked test_agent_graph.GraphTests.test_repeated_calls_terminate
```
