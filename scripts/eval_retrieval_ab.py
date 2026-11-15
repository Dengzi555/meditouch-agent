"""Paired 100-case real-section retrieval benchmark; no answer model is called."""
import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import platform
import random
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / 'eval_data/real_retrieval_100.json'
ARMS = ('lexical', 'hybrid', 'rerank')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def load_suite(items, path=SUITE):
    suite = json.loads(path.read_text(encoding='utf-8'))
    cases = suite['questions']
    if len(cases) < 100 or len({q['id'] for q in cases}) != len(cases):
        raise ValueError('Benchmark requires at least 100 distinct cases')
    if len({q['question'] for q in cases}) != len(cases):
        raise ValueError('Duplicate questions')
    indexed = {item['id']: item for item in items}
    for case in cases:
        if len(case['gold']) != 1 or not set(case['gold']) <= indexed.keys():
            raise ValueError(f"Invalid section gold: {case['id']}")
        item = indexed[case['gold'][0]]
        if digest(item['raw_quote'].encode()) != case['gold_text_sha256']:
            raise ValueError(f"Gold source changed: {case['id']}")
    return suite


def rank(hits, gold):
    return next((n for n, item in enumerate(hits, 1) if item['id'] in gold), None)


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def wilson(hits, total):
    z = 1.95996398454
    p = hits / total
    center = (p + z * z / (2 * total)) / (1 + z * z / total)
    delta = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(center - delta, 4), round(center + delta, 4)]


def summarize(rows):
    first = {row['id']: row for row in rows if row['repeat'] == 1}
    if not first:
        raise ValueError('Missing first round')
    for row in rows:
        if any(row[arm] != first[row['id']][arm] for arm in ARMS):
            raise ValueError('Ranking changed between repeats; inspect before reporting')
    output = []
    for arm in ARMS:
        ranks = [row[arm] for row in first.values()]
        n = len(ranks)
        hits = {k: sum(value is not None and value <= k for value in ranks) for k in (1, 3, 5)}
        latency = [row[f'{arm}_ms'] for row in rows]
        output.append({'arm': arm, 'questions': n, 'timed_requests': len(latency),
                       **{f'hit_at_{k}': value for k, value in hits.items()},
                       'hit5_wilson95': wilson(hits[5], n),
                       'mrr5': round(sum(1 / r for r in ranks if r) / n, 4),
                       'p50_ms': round(statistics.median(latency), 2),
                       'p95_ms': round(percentile(latency, .95), 2)})
    return output


def report_table(summary):
    lines = ['| 方案 | N | Hit@1 | Hit@3 | Hit@5 | MRR@5 | P50 ms | P95 ms |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    names = {'lexical': 'BM25', 'hybrid': '混合检索', 'rerank': '混合＋重排'}
    for row in summary:
        n = row['questions']
        hits = [f"{row[f'hit_at_{k}']}/{n} ({row[f'hit_at_{k}']/n:.1%})" for k in (1, 3, 5)]
        lines.append(f"| {names[row['arm']]} | {n} | {' | '.join(hits)} | {row['mrr5']:.4f} | {row['p50_ms']:.2f} | {row['p95_ms']:.2f} |")
    return '\n'.join(lines)


def run(repeats=3, seed=20261114, output=None):
    from meditouch.evidence_service import EvidenceService
    from meditouch.hybrid_retrieval import hybrid_search, MODEL_NAME as EMBEDDING
    from meditouch.query_plan import normalize_terms, requested_intents, retrieval_queries
    from meditouch.rag_pipeline import search
    from meditouch.reranking import rerank, MODEL_NAME as RERANKER

    items = EvidenceService().all_items()
    suite = load_suite(items)
    cases = suite['questions']
    output = output or ROOT / 'eval_results/retrieval_ab_source.csv'
    output.parent.mkdir(parents=True, exist_ok=True)
    # Freeze cases, corpus and code identity before collecting any scores.
    frozen = output.with_suffix('.corpus.json')
    frozen.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
    versions = {name: importlib.metadata.version(name) for name in ('fastembed', 'numpy', 'onnxruntime')}
    code = {path: digest((ROOT / path).read_bytes()) for path in (
        'scripts/eval_retrieval_ab.py', 'meditouch/hybrid_retrieval.py',
        'meditouch/rag_pipeline.py', 'meditouch/query_plan.py', 'meditouch/reranking.py')}
    manifest = {'started_utc': datetime.now(timezone.utc).isoformat(),
                'suite_sha256': digest(SUITE.read_bytes()), 'corpus_sha256': digest(frozen.read_bytes()),
                'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'runtime_code_sha256': code, 'python': platform.python_version(), 'platform': platform.platform(),
                'versions': versions, 'embedding': EMBEDDING, 'reranker': RERANKER,
                'repeats': repeats, 'seed': seed, 'k': 5, 'indexed_units': len(items),
                'query_preprocessing': 'shared normalize_terms + first retrieval_queries; no LLM rewrite',
                'rerank_pool': 'same hybrid top-5; no extra recall',
                'timing': 'warmed models/corpus, serial requests, shuffled questions each round; rerank_ms includes hybrid_ms',
                'scope': suite['scope'], 'provenance': suite['provenance']}
    manifest_path = output.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    warmed, mode = hybrid_search(cases[0]['question'], items, 5)
    if mode != 'hybrid':
        raise RuntimeError(f'Expected actual hybrid retrieval, got {mode}')
    rerank(cases[0]['question'], warmed)
    rows = []
    with output.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = None
        for repeat in range(1, repeats + 1):
            order = list(cases)
            random.Random(seed + repeat).shuffle(order)
            for index, case in enumerate(order, 1):
                query = retrieval_queries(normalize_terms(case['question']), requested_intents(case['question']))[0]
                started = time.perf_counter()
                lexical = search(query, items, 5)
                lexical_ms = (time.perf_counter() - started) * 1000
                started = time.perf_counter()
                hybrid, mode = hybrid_search(query, items, 5)
                hybrid_ms = (time.perf_counter() - started) * 1000
                if mode != 'hybrid':
                    raise RuntimeError(f"Unexpected {mode} for {case['id']}")
                started = time.perf_counter()
                reranked = rerank(query, hybrid)
                rerank_only_ms = (time.perf_counter() - started) * 1000
                row = {'id': case['id'], 'repeat': repeat, 'split': case['split'],
                       'topic': case['topic'], 'kind': case['kind'], 'query': query,
                       'targets': '|'.join(case['gold']), 'mode': mode, 'target_level': 'section',
                       'lexical': rank(lexical, case['gold']), 'hybrid': rank(hybrid, case['gold']),
                       'rerank': rank(reranked, case['gold']),
                       'lexical_ids': '|'.join(i['id'] for i in lexical),
                       'hybrid_ids': '|'.join(i['id'] for i in hybrid),
                       'rerank_ids': '|'.join(i['id'] for i in reranked),
                       'lexical_ms': round(lexical_ms, 3), 'hybrid_ms': round(hybrid_ms, 3),
                       'rerank_ms': round(hybrid_ms + rerank_only_ms, 3)}
                if writer is None:
                    writer = csv.DictWriter(stream, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                stream.flush()
                rows.append(row)
                if index % 20 == 0:
                    print(f'round {repeat}/{repeats}: {index}/{len(cases)}', flush=True)
    summary = summarize(rows)
    manifest['completed_utc'] = datetime.now(timezone.utc).isoformat()
    manifest['summary'] = summary
    manifest['by_kind'] = {kind: summarize([r for r in rows if r['kind'] == kind])
                           for kind in ('direct', 'colloquial')}
    manifest['by_topic'] = {topic: summarize([r for r in rows if r['topic'] == topic])
                            for topic in sorted({c['topic'] for c in cases})}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    report = '# 真实公开资料检索：100题\n\n' + report_table(summary)
    report += f'\n\n100题、100个不同小节、20篇NHS资料；50直接、50口语。{repeats}轮共{len(rows)}次/方案，准确率分母仍为100，延迟汇总所有热启动请求。\n'
    report += '\n固定现有检索配置，不根据本轮结果调参；重排只重排混合Top-5，不扩大召回。查询预处理三组共用，不包含LLM改写或回答生成。部分金标小节沿用旧资料，不是独立盲测；未经医学专家审核，不代表医疗正确率。\n'
    report += '\nHit@5 Wilson 95%区间：' + '；'.join(f"{r['arm']} {r['hit5_wilson95']}" for r in summary) + '。\n'
    output.with_suffix('.md').write_text(report, encoding='utf-8')
    print(report, flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('source',), default='source')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--seed', type=int, default=20261114)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    run(args.repeats, args.seed, args.out)


if __name__ == '__main__':
    main()
