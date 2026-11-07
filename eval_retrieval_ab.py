"""Paired retrieval A/B: BM25 vs BM25+BGE (RRF) vs +cross-encoder rerank.

Compare the same retrieval arms on 24 development and 60 validation queries.

Both arms see the same index and the same k; rerank only reorders the hybrid
top-k. Latency is reported as p50/p95; embeddings and the reranker are warmed
before measurement.
"""
import argparse
import csv
import json
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).parent

TARGETS = {
    1: ('adult-common-cold', 'cq-respiratory-home-observation', 'cdc-rhinovirus'),
    2: ('adult-common-cold',),
    3: ('adult-common-cold', 'cdc-respiratory-care'),
    4: ('adult-common-cold', 'cq-respiratory-home-observation'),
    5: ('nhc-child-infectious-diarrhea-2020-2', 'beijing-child-gastroenteritis-2023-1'),
    6: ('nhc-child-infectious-diarrhea-2020-1', 'beijing-child-gastroenteritis-2023-2'),
    7: ('cdc-flu-faq', 'cdc-flu-myths', 'nhc-influenza-guideline-2025-4a45', 'cdc-rhinovirus'),
    8: ('nhc-influenza-guideline-2025-ad1', 'cdc-flu-myths', 'beijing-flu-prevention'),
    9: ('cq-respiratory-home-monitoring',),
    10: ('cq-respiratory-home-monitoring', 'cq-respiratory-home-observation'),
    11: ('nhc-hypertension-food-2023-e0b', 'nhc-hypertension-food-2023-e004'),
}


def p95(values):
    if len(values) < 2:
        return values[0] if values else 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(0.95 * (len(ordered) - 1))))
    return ordered[index]


def source_rows():
    from evidence_service import EvidenceService
    from hybrid_retrieval import hybrid_search
    from query_plan import normalize_terms, requested_intents, retrieval_queries
    from rag_pipeline import search
    from reranking import rerank

    questions = json.loads((ROOT / 'eval_data/simulated_patient_questions.json').read_text(encoding='utf-8'))
    items = EvidenceService().all_items()
    if not items:
        raise SystemExit('本地索引缺失，无法运行 source 模式')
    # Warm the embedding model before timing.
    hybrid_search('普通感冒护理', items, 5)

    def rank(hits, targets, exact):
        for position, item in enumerate(hits, 1):
            if (item['id'] in targets if exact else item['id'].startswith(targets)):
                return position
        return None

    rows = []
    first_by_group = {}
    indexed_ids = {item['id'] for item in items}
    for case in questions:
        original = case['question']
        group = case.get('group')
        prior = first_by_group.setdefault(group, original)
        question = f'{original}（前文：{prior}）' if case['kind'] == 'followup' else original
        query = retrieval_queries(normalize_terms(question), requested_intents(question))[0]
        exact = 'gold' in case
        targets = tuple(case['gold']) if exact else TARGETS[group]
        if exact and not set(targets) <= indexed_ids:
            raise SystemExit(f"Missing gold section for {case['id']}: {targets}")
        started = time.perf_counter()
        lexical = search(query, items, 5)
        lexical_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        hybrid, mode = hybrid_search(query, items, 5)
        hybrid_ms = (time.perf_counter() - started) * 1000
        if mode != 'hybrid':
            raise SystemExit(f'Expected hybrid retrieval, got {mode}')
        started = time.perf_counter()
        reranked = rerank(query, hybrid)
        rerank_ms = (time.perf_counter() - started) * 1000
        rows.append(dict(id=case['id'], split=case['split'], group=group, kind=case['kind'],
                         mode=mode, target_level='section' if exact else 'source',
                         targets='|'.join(targets), lexical=rank(lexical, targets, exact),
                         hybrid=rank(hybrid, targets, exact), rerank=rank(reranked, targets, exact),
                         lexical_ms=round(lexical_ms, 1), hybrid_ms=round(hybrid_ms, 1),
                         rerank_ms=round(rerank_ms, 1)))
    return rows


def summarize(rows, split):
    included = {'legacy_validation', 'fresh_validation'} if split == 'validation' else {split}
    subset = [row for row in rows if row['split'] in included]
    output = []
    for arm in ('lexical', 'hybrid', 'rerank'):
        ranks = [row[arm] for row in subset]
        hits = [sum(value is not None and value <= k for value in ranks) for k in (1, 3, 5)]
        mrr = sum(1 / value for value in ranks if value) / len(ranks)
        latencies = [
            row['lexical_ms'] if arm == 'lexical'
            else row['hybrid_ms'] + (row['rerank_ms'] if arm == 'rerank' else 0)
            for row in subset
        ]
        output.append(dict(split=split, arm=arm, questions=len(subset),
                            hit_at_1=hits[0], hit_at_3=hits[1], hit_at_5=hits[2],
                            mrr=round(mrr, 3),
                            p50_ms=round(statistics.median(latencies), 1),
                            p95_ms=round(p95(latencies), 1)))
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('source',), default='source')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    rows = source_rows()
    if not rows:
        raise SystemExit('没有可评测的样本')
    splits = ('development', 'legacy_validation', 'fresh_validation', 'validation')
    summary = [record for split in splits for record in summarize(rows, split)]
    print(f'All labeled cases: {len(rows)}')
    print('| split | arm | N | Hit@1 | Hit@3 | Hit@5 | MRR@5 | p50 ms | p95 ms |')
    print('| --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for record in summary:
        print('| {split} | {arm} | {questions} | {hit_at_1} | {hit_at_3} | {hit_at_5} | '
              '{mrr} | {p50_ms} | {p95_ms} |'.format(**record))
    output = args.out or ROOT / 'eval_results' / f'retrieval_ab_{args.mode}.csv'
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(output)


if __name__ == '__main__':
    main()
