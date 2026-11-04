"""Measure whether expected sources and sections appear in the first three hits."""
import json
from pathlib import Path

from evidence_service import EvidenceService

ROOT = Path(__file__).parent


def main():
    cases = json.loads((ROOT / 'eval_data/source_retrieval_cases.json').read_text(encoding='utf-8'))
    service = EvidenceService()
    rows = []
    for case in cases:
        hits = service.search(case['question'], k=3)['items']
        rank = next((n for n, hit in enumerate(hits, 1)
                     if hit['document_id'] == case['document_id']
                     and case.get('section', '') in ' '.join(hit['section_path'])), None)
        rows.append({**case, 'rank': rank, 'retrieved_ids': [hit['id'] for hit in hits]})
    report = {
        'scope': 'Small source and section retrieval smoke check',
        'total': len(rows),
        'hit_at_1': sum(row['rank'] == 1 for row in rows),
        'hit_at_3': sum(row['rank'] is not None for row in rows),
        'cases': rows,
    }
    (ROOT / 'data/source-retrieval-report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))
    return int(report['hit_at_3'] != report['total'])


if __name__ == '__main__':
    raise SystemExit(main())
