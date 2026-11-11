"""Run five deterministic Agent conversations without a key or downloaded sources."""
import json
import sys
from pathlib import Path

from scripts.agent_acceptance import FailingService, ScriptedModel
from meditouch.agent_graph import run_graph
from meditouch.evidence_service import EvidenceService, normalize_document

CORPUS = Path(__file__).resolve().parent.parent / 'demo_corpus' / 'documents.json'


def run_demo():
    documents = json.loads(CORPUS.read_text(encoding='utf-8'))
    items = [normalize_document(document) for document in documents]
    normal = EvidenceService(items=[item for item in items if item['id'] != 'demo-other'])
    conflict = EvidenceService(items=items)
    cases = [
        ('normal', '演示措施X的原文是什么？', 'answered', normal),
        ('clarification', '我可以补钾吗？', 'needs_clarification', normal),
        ('insufficient', 'zzzzunknown', 'insufficient', normal),
        ('conflict', '演示措施X有哪些分歧？', 'unresolved', conflict),
        ('tool_failure', '演示措施X的原文是什么？', 'tool_error', FailingService()),
    ]
    rows = []
    for scenario, question, expected, service in cases:
        result = run_graph(question, corpus='demo', service=service, model=ScriptedModel(scenario))
        rows.append({
            'scenario': scenario,
            'question': question,
            'expected': expected,
            'actual': result['status'],
            'passed': result['status'] == expected,
            'answer': result['answer'],
            'tool_calls': result['tool_calls'],
        })
    return rows


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    rows = run_demo()
    print(json.dumps({'scope': '合成语料，仅工程演示', 'cases': rows}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if all(row['passed'] for row in rows) else 1)


if __name__ == '__main__':
    main()
