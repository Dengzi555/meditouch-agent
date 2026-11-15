# 需求理解与证据状态

药店工作流分别保存需求理解、证据来源、库存查询和安全筛查状态，由各节点负责更新。

| 层次 | 主要字段 | 用途 |
| --- | --- | --- |
| 需求理解 | `intent`、`standalone_query`、`context_resolution` | 判断任务、补全指代与查询主题 |
| 顾客信息 | `symptoms`、`denied_symptoms`、`customer_tags` | 保存明确肯定、否认和人群信息 |
| 健康解释 | `health_status`、`health_sources`、`health_card` | 组织来源、正文与引用 |
| 库存结果 | `inventory_results`、`inventory_status` | 展示本店商品、货架与库存 |
| 安全筛查 | `safety_status`、`excluded_drugs`、`excluded_reasons` | 记录命中禁忌与信息缺口，约束候选重查 |
| 应急记录 | 应急卡的 `facts`、`revision`、`handover` | 保存店员确认的现场观察和交接摘要 |

需求理解决定进入哪条路径，证据与工具结果决定输出哪些内容。库存数量来自数据库，健康解释保留来源，急救指引由宿主程序构建。

实现见[药店工作流](pharmacy-copilot.md)、[健康问答](pharmacy-health-qa.md)、[风险路径](patient-flow-and-acceptance.md)与[应急处置](emergency-support.md)。评测结果见[检索评测](eval-report.md)和[工作流评测](evaluation/pharmacy-agentic-rag-30.md)。
