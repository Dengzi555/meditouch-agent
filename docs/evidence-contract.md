# evidence.v1 统一检索契约

请求：question为1—20000字符非空字符串；k为1—8整数；corpus为real或demo。corpus由请求宿主确定，不接受模型传入任意文件路径、URL、审核开关。

```json
{
  "schema_version": "evidence.v1",
  "status": "ok",
  "query": "问题",
  "corpus": "real",
  "items": [],
  "related_items": [],
  "coverage": {
    "retrieved": 0,
    "related": 0,
    "indexed": 0,
    "conflict_detection": "structured_claims_only"
  },
  "limitations": []
}
```

items是按BM25相关性排序的前k项；related_items补充相同显式主张范围的其他证据，防止反证和替代版本被top-k截掉。相关性仍不代表医学可信。

每项条目字段：

| 字段 | 含义 |
| --- | --- |
| id / document_id | 证据单元ID / 原文档ID |
| source | title、publisher、date、url、sha256；未知值保留null |
| pdf_pages / printed_pages | PDF物理页 / 印刷页；旧演示条目无定位时为空数组 |
| text | 保留章节结构的检索展示文本 |
| raw_quote | 原始提取行拼接的完整引用单元 |
| spans | page、printed_page、raw_start、raw_end、raw_quote；偏移为提取文本字符偏移，不是PDF字节偏移 |
| section_path | 章节路径 |
| review_status / generation_allowed | 审核状态 / 可生成标记；真实资料要求文档与块双重审核 |
| demo | 是否虚构测试资料，不能与真实语料混用 |
| claims / supersedes | 显式结构化主张 / 版本替代关系，不自动从未知事实补全 |
| quality_flags | 版式、脚注、单位、审核等质量限制 |
| score | BM25分数，不是置信概率 |
| snapshot_sha256 | 除score和本字段外条目的规范化JSON哈希，仅用于本次快照一致性 |

真实语料加载时核对PDF哈希、manifest对应关系、原文span偏移、页码和重建的text。哈希一致不代表医学正确、来源权威或版本现行。

工具错误统一返回：

```json
{
  "schema_version": "evidence.v1",
  "status": "error",
  "error": {"code": "CORPUS_UNAVAILABLE", "retryable": false}
}
```

本地缺库和完整性错误不自动重试。暂时连接或超时可重试一次。不得把异常原文（可能含凭据）透传给模型或前端。

共享实现：meditouch/evidence_service.py。MCP使用search_materials/read_material；HTTP使用/api/evidence/search；LangGraph白名单工具在同进程调用相同服务。旧接口继续保留，不声称旧接口字段已经全部迁移。
