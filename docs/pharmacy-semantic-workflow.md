# 多轮语义理解与知识候选流程

首批支持五类工程场景：基础找药、禁忌拦截、联合用药核查、缺货替代检索、急症终止。
覆盖的是执行路径，不代表完整病种覆盖或经过医学验证。

## 输入和记忆

`POST /api/agent/stream` 接收 `question`、`workspace` 以及可选 `history`。
history 最多20条，仅接受 user/assistant，每条不超过2000字符。前端随当前聊天发送；新咨询不带旧聊天。
这是请求级对话记忆，不是跨浏览器、跨账号的持久病历或 LangGraph checkpoint。

语义节点输出 intent、symptoms、medical_topics、target_drugs、customer_tags、denied_symptoms。
四种意图为 find_drug、check_safety、emergency、chitchat。模型完成口语归一化、指代解析与历史更新；
否认症状不能并入肯定列表，助手曾给出的候选不能当成顾客已经服用的药。
格式异常进入人工核对，不拿原句猜药。急症另有宿主风险检查，即使模型不可用仍能终止。

## 查询及替代

`medical_topics` 保存明确咨询的疾病/健康主题，不代表已确诊。例如“感冒吃啥”保留主题“感冒”，不猜测发热、咳嗽，也不把整句话当成药名查询。
主题可触发通用目录字面查询和知识补查，不为每个疾病写路由规则。目录匹配商品标记 `topic_only`，保留库存与货架事实，不能作为已核实适应症的用药推荐。
知识补查失败后仍可保留这些已查到的本店商品；没有任何可解析条件时先要求明确查询内容，不误报本店没有药。

批量查询用参数化 OR 条件匹配药名、品牌，按 SKU 去重；返回 matched_terms 与 exact/fuzzy 标记。
宽泛匹配不会宣称等价。超过50条视为范围过大，要求缩小范围。
每批候选必须经过安全核查。命中禁忌排除，库存为0记录缺货；两者作为后续知识补查的排除条件。
既有在库候选可以保留，但替代候选必须重新核查。

最多三轮库存、两轮知识补查；空结果、格式错误和异常均可终止，不无限搜索。
没有已知症状时，不从药名猜顾客病因来自动替换。

## 知识来源

`query_medical_rag(symptoms, customer_tags, excluded_drugs)` 使用现有 EvidenceService 检索真实本地资料。
模型只从检索内容选择药名；引用 ID 必须存在，药名必须在对应原文出现。
这验证可追溯性，不证明原文适用性或候选的医学正确性。
当资料不足，允许返回 source=llm_knowledge 的保守检索词，UI 明确标明缺少可核对依据，且不附伪造引用。
设置 `MED_AGENT_ALLOW_KNOWLEDGE_CANDIDATES=0` 可以关闭此兜底。

检索和模型文本均不授权售药、不生成剂量。现有数据库没有完整适应症、配伍和相互作用库；
所以不能保证“无禁忌替代”，也不能把蒙脱石散机械替换为洛哌丁胺。
联合用药始终保持 unknown；有成分记录时提示重复成分，其余交药师确认。

验证：`python -m unittest tests.test_pharmacy_semantic_workflow tests.test_pharmacy_graph tests.test_api_sse -v`
