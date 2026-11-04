"""Run the flu pilot holdout against the installed reviews and real model."""
import json
from pathlib import Path

from agent_graph import run_graph
from evidence_service import EvidenceService
from reviewed_content import statement_catalog

ROOT = Path(__file__).parent


def main(service=None, runner=run_graph, root=ROOT):
    service = EvidenceService() if service is None else service
    catalog = statement_catalog(service.all_items())
    spec = json.loads((root / 'eval_data/flu_pilot_holdout.json').read_text(encoding='utf-8'))
    required = {sid for case in spec['cases'] for sid in case['required_statement_ids']}
    missing = required - catalog.keys()
    if missing:
        print(json.dumps({'status': 'awaiting_review_install', 'missing': sorted(missing), 'executed': 0}))
        return 2
    rows = []
    for case in spec['cases']:
        result = runner(case['question'], service=service)
        statements = result.get('statements', [])
        ids = {statement['statement_id'] for statement in statements}
        citations = {(c.get('statement_id'), c['evidence_id'], c['quote']) for c in result['citations']}
        bound = all(
            sid in catalog and (sid, catalog[sid][0]['id'], catalog[sid][0]['raw_quote']) in citations
            and catalog[sid][1]['limitations'] in result['answer']
            and catalog[sid][1]['applicability'] in result['answer']
            for sid in ids
        )
        passed = (result['status'] == case['expected_status'] and bound
                  and set(case['required_statement_ids']) <= ids <= required
                  and (case['expected_status'] == 'answered' or not (citations or ids))
                  and {sid for sid, _, _ in citations} == ids
                  and case.get('required_limit', '') in result['answer'])
        rows.append(dict(id=case['id'], actual=result['status'], passed=passed,
                         answer=result['answer'], citations=result['citations']))
    report = dict(passed=sum(row['passed'] for row in rows), total=len(rows), cases=rows)
    (root / 'data/flu-pilot-acceptance.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('passed', 'total')}))
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
