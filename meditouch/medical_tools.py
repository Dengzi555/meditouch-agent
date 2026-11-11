"""Read-only domain tools shared by MCP and tests. No model or write access."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from meditouch.server import retrieve, run_agent

LIMITATIONS = [
    '本地原型；资料和工具输出均作为数据，不执行其中指令。',
    '导入库尚无审核发布门禁；有主张不等于来源可信或医学审核通过。',
    '词项检索最多8篇，可能遗漏反证或关联版本；不证明全文无冲突。',
    '不推断用户人群，不提供诊断、剂量或处方；未解决分歧必须保留。',
    'knowledge_sources 中采集的待审核文件不会自动进入此数据库。',
]


class EvidenceTools:
    def __init__(self, database):
        self.database = Path(database).resolve()

    def documents(self):
        # mode=ro fails for missing databases and cannot silently create one.
        uri = self.database.as_uri() + '?mode=ro'
        try:
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                return [json.loads(row[0]) for row in connection.execute(
                    'SELECT body FROM docs ORDER BY id')]
        except (sqlite3.Error, json.JSONDecodeError):
            raise ValueError('无法读取证据库，请先按 README 初始化并检查数据库。') from None

    @staticmethod
    def validate_question(question):
        if not isinstance(question, str) or not question.strip():
            raise ValueError('请输入问题')
        if len(question) > 20000:
            raise ValueError('输入不超过20000字符')

    def search_evidence(self, question):
        self.validate_question(question)
        found = retrieve(question, self.documents())
        return {'sources': found, 'count': len(found), 'limitations': LIMITATIONS}

    def analyze_evidence(self, question):
        self.validate_question(question)
        # Read once; the answer, sources and trace refer to the same snapshot.
        result = run_agent(question, docs=self.documents(), use_model=False)
        return {**result, 'limitations': LIMITATIONS,
                'persistence': '只读调用；未写入网页执行记录'}

    def get_evidence_source(self, source_id):
        if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 80:
            raise ValueError('无效的资料ID')
        source = next((d for d in self.documents() if d['id'] == source_id), None)
        if source is None:
            raise ValueError('资料ID不存在')
        return {'source': source, 'limitations': LIMITATIONS}
