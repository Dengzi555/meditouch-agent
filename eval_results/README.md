# Claw Eval 医疗自定义评测

## 本轮结果

2026年10月30日执行18个自定义任务，每题3次，共54次全部通过；严格三次全过为18/18。
10题覆盖规则证据流程，8题覆盖高血压正文检索。结果是本地开发回归，不是Claw-Eval官方300题成绩，
不是Craft客户端模型端到端成绩，也不代表医学准确率。

`latest.json` 指向最近一次完整运行目录；目录内 `summary.json` 保存逐题统计，54份JSONL保存实际执行和评分轨迹。
失败也会记入结果，不自动丢弃失败重试。所有业务调用禁用模型，使用合成证据或待审核资料。

## 实际接入范围

复用用户提供快照的 TaskDefinition、AbstractGrader、动态get_grader、DimensionScores、
compute_task_score、pass@k/pass_hat_k、TraceWriter和load_trace。
自定义runner直接运行本项目领域函数，并用上游轨迹结构记录真实输入输出；不是让模型模拟回答，
也未使用上游模型Agent Loop。源快照提交为5680b8b11ff2ee5dd2b07b89086a29a5c5c984d7，
运行前校验vendor中43个上游文件的Git blob SHA。

## 测试内容

- 规则链路：未解决分歧、显式替代、日期不能单独决定、不同人群隔离、演示隔离。
- 边界：未知问题、空证据、报告模式限制、规则模式输入指令、空输入拒绝。
- RAG：8条目标证据命中、页码与锚点、待审核标记、每条原文引用的偏移一致性。
- 只读：调用前后业务数据库SHA256一致。
- 轨迹：写入后用上游reader重新加载，重新评分必须一致。
- 评分器反例：错误状态、数据库变化、缺失审计与伪造引用必须不能通过。

## 评分口径

每题所有自定义必需检查均通过才记completion=1，否则为0；数据库不变且无意外执行错误时safety=1。
任务分数使用上游公式 safety × (0.8 × completion + 0.2 × robustness)，阈值0.75。
此运行没有故障注入，上游robustness默认给干净执行1分，因此不能解释为已经验证故障恢复。
三次均为全新内存输入下的确定性执行，不代表三次独立模型采样。

上游compute_pass_hat_k实现是(c/n)^k估计，与“观测到三次全过”不是同一统计量。
报告同时保留upstream_pass_hat_3与strict_all_trials_pass，后者用于本次严格重复通过统计。

## 复现

```powershell
cd D:\demo
python -m venv .venv-eval
.\.venv-eval\Scripts\python.exe -m pip install .\vendor\claw-eval
$env:PYTHONUTF8='1'
.\.venv-eval\Scripts\python.exe run_claw_medical_eval.py
.\.venv-eval\Scripts\python.exe -m unittest -v
```

eval_tasks内生成的18份task.yaml和grader.py符合本地框架的任务模型；自定义runner负责业务适配。
原始claw-eval目录未改动，依赖安装与构建均使用D:\demo中的副本。

## 官方模型评测未运行的原因

提供的快照缺少官方tasks目录、fixtures和sandbox脚本；当前进程未配置MED_AGENT_API_KEY、
OPENROUTER_API_KEY或OPENAI_API_KEY。本轮只检查是否存在，不读取认证文件或输出凭据。
官方模型Agent Loop及LLM judge需要另行准备这些资源和模型端点；当前没有启动Docker或对外发送模型请求。
正式扩展时应独立报告“官方通用Agent基准”和“本项目医疗领域用例”，不把两者成绩合并。
