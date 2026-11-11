# 感冒与流感科普审核单

两条候选记录均待审。已核对两条记录的完整原文及快照指纹与当前语料一致。

请打开 [候选记录](../data/flu-pilot-review.json)，逐条对照其中的 source_url 和 source_quote。

请同时审核 applicability_policy。question_terms 是原问题必须命中其一的短语，populations 是允许的人群声明，conditions 是必须明确匹配的结构化字段。空人群列表和空条件对象仅用于审核人员确认没有额外限制的一般科普。候选记录中的短语只是待审建议。校验采用字面匹配，可能漏掉同义表达，需在预留题中复核。

| 条目 | 核对重点 |
| --- | --- |
| flu-pilot-01 | 病原体区别是否表述准确，是否容易被读成可以判断个人感染类型 |
| flu-pilot-02 | 典型表现是否保留症状重叠及不能仅凭症状确诊的限制 |

逐条记录通过、需修改或不通过。通过条目请核定 patient_text、applicability、limitations，并填写 reviewer、reviewed_at（YYYY-MM-DD）与 status=approved。修改表述后请重新核对。保留证据 ID、完整原文和快照指纹。

请同时核对 [四道预留题](../eval_data/flu_pilot_holdout.json)。前两题检查科普回答及引用，后两题检查个体确诊与当日感染率的证据边界。若审核修订了限制条件，请同步复核第二题的 required_limit。

收到审核结果后，从项目目录执行以下命令。安装前核对待安装清单，安装会替换当前审核注册表并备份原文件。

```powershell
rtk proxy python -B -m meditouch.reviewed_content check data/flu-pilot-review.json
rtk proxy python -B -m meditouch.reviewed_content install data/flu-pilot-review.json
rtk proxy pwsh -NoProfile -File start_with_model.ps1 -Action FluPilotEval
```

专项验收要求两条候选均已通过并导入。若只通过一条，先记录另一条的修改意见，完成复审后再执行四题。执行报告保存至 data/flu-pilot-acceptance.json。
