# 呼吸道健康补充资料下载清单

处理更新（2026-11-02）：本批5份已清理并构建17个待审核检索单元，接入真实EvidenceService；此前仅下载的状态已完成升级。仍未医学审核，仍禁止正式回答生成。实现见 `ingestion-and-consultations.md`，审核清单在 `rag_data/respiratory_reference/REVIEW_QUEUE.md`。

采集日期：2026-11-02。用户要求下载可供后续RAG使用的公开资料。本批实际成功5份（1份PDF、4份HTML），另3个网页返回HTTP 412，未伪装为下载成功。与此前4篇疾控科普合计9份呼吸道相关原件，不计高血压材料。

本批目录：`D:\demo\knowledge_sources\respiratory_reference_20261102\`。目录中的 `manifest.json` 记录来源、日期、下载结果、字节数、SHA256、用途与待审核状态；带时间戳的manifest保存每次采集结果，原件变更时按哈希另存，避免覆盖历史快照。源码脚本：`download_patient_materials.py`，运行需Python及pypdf。

| 已成功保存的材料 | 用途 | 原件 |
| --- | --- | --- |
| [流行性感冒诊疗方案（2025年版）](https://www.nhc.gov.cn/ylyjs/zcwj/202501/f8fcecca59a048bebc4a71847ce57594/files/1741764832851_94226.pdf) | 医疗专业诊疗参考，不能直接当作患者处方 | `nhc-influenza-guideline-2025.pdf`，18页 |
| [呼吸道感染性疾病如何居家监测？](https://wsjkw.cq.gov.cn/ztzl_242/jkkp/202401/t20240117_12828443.html) | 症状与基础疾病监测科普 | `cq-respiratory-home-monitoring-2024.html` |
| [什么情况下适合居家治疗观察？](https://wsjkw.cq.gov.cn/ztzl_242/jkkp/202501/t20250102_14038341_zzb.html) | 居家观察与就医信息 | `cq-respiratory-home-observation-2025.html` |
| [流感如何“未病先防”？](https://wjw.beijing.gov.cn/bmfw_20143/jkzs/jksh/202609/t20260918_4869326.html) | 流感防护与避免叠加用药科普 | `beijing-flu-prevention-20260918.html` |
| [甲流高热不退？这些退热方法需知道！](https://wjw.beijing.gov.cn/bmfw_20143/jkzs/jksh/202501/t20250123_3997070.html) | 发热照护和重复用药风险科普 | `beijing-flu-fever-care-20250123.html` |

PDF附有 `.pdf.pages.json` 的逐页文字抽取，物理页码从1开始；抽取文件亦保留哈希。正文标题与PDF类型已核对，表格、剂量、阅读顺序与临床适用性尚未逐项审核。发布日期与文件落款日期分别记录：方案页面发布于2025-01-22，落款2025-01-20。重庆居家观察页正文标注2025-01-01，不能因URL含20250102就改写发布日期。

本批只完成下载与初步校验，未进入现有RAG索引，全部 `pending`、`generation_allowed=false`。此前4篇疾控网页仍按原设置可检索预览。下一步是清理网页正文、按完整问答/章节切分、核对引用与适用条件，再接入统一证据接口；不能把“成功下载”等同于“已医学审核”。本批安全用药科普不是具体厂家/规格的药品说明书，未据此建立剂量工具。

失败记录：国家卫健委2025版流感方案通知页、2025-11-21与2025-12-19发布会实录页均返回HTTP 412；附件PDF成功独立下载。失败网页的链接和错误保留在manifest，未用搜索摘要冒充原始正文。

原始资料位于Git忽略目录，不随源码上传；公开访问不等于确认商业再分发授权。本批不判断2025方案是否仍为最新有效版本。
