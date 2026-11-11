"""Retrieval regression on 200 synthetic questions with paragraph-level gold labels."""
import argparse
import json
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / 'eval_corpus' / 'symptom_retrieval_200' / 'documents.json'
SUITE = ROOT / 'eval_data' / 'retrieval_suite_200.json'


def p95(values):
    if len(values) < 2:
        return values[0] if values else 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(0.95 * (len(ordered) - 1))))]


def load_chunks():
    corpus = json.loads(CORPUS.read_text(encoding='utf-8'))
    chunks = []
    for document in corpus['documents']:
        for paragraph in document['paragraphs']:
            chunks.append(dict(id=paragraph['id'], text=paragraph['text'],
                               symptom=document['symptom'], document_id=document['id']))
    return chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-rerank', action='store_true')
    parser.add_argument('--out', type=Path, default=ROOT / 'eval_results' / 'retrieval_symptom_200.csv')
    args = parser.parse_args()
    from meditouch.hybrid_retrieval import hybrid_search
    from meditouch.rag_pipeline import search
    from meditouch.reranking import rerank

    chunks = load_chunks()
    questions = json.loads(SUITE.read_text(encoding='utf-8'))['questions']
    hybrid_search(questions[0]['question'], chunks, 5)

    def rank(hits, gold):
        for position, item in enumerate(hits, 1):
            if item['id'] in gold:
                return position
        return None

    rows = []
    for case in questions:
        started = time.perf_counter()
        lexical = search(case['question'], chunks, 5)
        lexical_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        hybrid, mode = hybrid_search(case['question'], chunks, 5)
        hybrid_ms = (time.perf_counter() - started) * 1000
        reranked = []
        rerank_ms = 0.0
        if not args.skip_rerank:
            started = time.perf_counter()
            reranked = rerank(case['question'], hybrid)
            rerank_ms = (time.perf_counter() - started) * 1000
        gold = set(case['gold'])
        rows.append(dict(id=case['id'], symptom=case['symptom'], variant=case['variant'],
                         split=case['split'], mode=mode,
                         lexical=rank(lexical, gold), hybrid=rank(hybrid, gold),
                         rerank=rank(reranked, gold) if reranked else None,
                         lexical_ms=round(lexical_ms, 1), hybrid_ms=round(hybrid_ms, 1),
                         rerank_ms=round(rerank_ms, 1)))

    def summarize(subset):
        output = []
        for arm in ('lexical', 'hybrid', 'rerank'):
            if arm == 'rerank' and args.skip_rerank:
                continue
            ranks = [row[arm] for row in subset]
            hits = [sum(value is not None and value <= k for value in ranks) for k in (1, 3, 5)]
            mrr = sum(1 / value for value in ranks if value) / len(ranks)
            latencies = [row['lexical_ms'] if arm == 'lexical'
                         else row['hybrid_ms'] + (row['rerank_ms'] if arm == 'rerank' else 0)
                         for row in subset]
            output.append(dict(arm=arm, n=len(subset), hit1=hits[0], hit3=hits[1], hit5=hits[2],
                               mrr=round(mrr, 3), p50=round(statistics.median(latencies), 1),
                               p95=round(p95(latencies), 1)))
        return output

    def emit(title, subset):
        print(f'--- {title}（N={len(subset)}）')
        print('| arm | Hit@1 | Hit@3 | Hit@5 | MRR@5 | p50 ms | p95 ms |')
        print('| --- | --- | --- | --- | --- | --- | --- |')
        for record in summarize(subset):
            print(f"| {record['arm']} | {record['hit1']} | {record['hit3']} | {record['hit5']} | "
                  f"{record['mrr']} | {record['p50']} | {record['p95']} |")

    for split in ('development', 'regression', 'all'):
        emit(split, [row for row in rows if split in (row['split'], 'all')])
    for variant in ('standard', 'colloquial', 'long_tail'):
        emit(variant, [row for row in rows if row['variant'] == variant])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = __import__('csv').DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(args.out)


if __name__ == '__main__':
    main()