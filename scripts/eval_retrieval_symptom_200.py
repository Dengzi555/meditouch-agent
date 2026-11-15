"""Serial, warmed retrieval regression with frozen synthetic corpus and paragraph gold."""
import argparse
import csv
import importlib.metadata
import json
import platform
import random
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.eval_retrieval_ab import digest, rank, report_table, summarize

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / 'eval_corpus/symptom_retrieval_200/documents.json'
SUITE = ROOT / 'eval_data/retrieval_suite_200.json'


def load_chunks():
    corpus = json.loads(CORPUS.read_text(encoding='utf-8'))
    return [dict(id=paragraph['id'], text=paragraph['text'], symptom=document['symptom'], document_id=document['id'])
            for document in corpus['documents'] for paragraph in document['paragraphs']]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--seed', type=int, default=20261115)
    parser.add_argument('--out', type=Path, default=ROOT / 'eval_results/retrieval_symptom_200.csv')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    from meditouch.hybrid_retrieval import hybrid_search, MODEL_NAME as EMBEDDING
    from meditouch.rag_pipeline import search
    from meditouch.reranking import rerank, MODEL_NAME as RERANKER

    chunks = load_chunks()
    questions = json.loads(SUITE.read_text(encoding='utf-8'))['questions']
    if len(questions) != 200 or len({case['id'] for case in questions}) != 200:
        raise ValueError('Expected 200 unique questions')
    if any(not set(case['gold']) <= {item['id'] for item in chunks} for case in questions):
        raise ValueError('Invalid paragraph gold')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    code_paths = ['scripts/eval_retrieval_symptom_200.py', 'scripts/eval_retrieval_ab.py',
                  'meditouch/hybrid_retrieval.py', 'meditouch/rag_pipeline.py', 'meditouch/reranking.py']
    manifest = {
        'started_utc': datetime.now(timezone.utc).isoformat(), 'questions': len(questions), 'chunks': len(chunks),
        'suite_sha256': digest(SUITE.read_bytes()), 'corpus_sha256': digest(CORPUS.read_bytes()),
        'git_head': subprocess.check_output(['rtk', 'proxy', 'git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'runtime_code_sha256': {name: digest((ROOT / name).read_bytes()) for name in code_paths},
        'python': platform.python_version(), 'platform': platform.platform(),
        'versions': {name: importlib.metadata.version(name) for name in ('fastembed', 'numpy', 'onnxruntime')},
        'embedding': EMBEDDING, 'reranker': RERANKER, 'repeats': args.repeats, 'seed': args.seed, 'k': 5,
        'scope': '10类症状，120段合成工程夹具，200题段落级检索回归。',
        'query_preprocessing': 'raw question shared by all arms',
        'rerank_pool': 'same hybrid top-5; no extra recall',
        'timing': 'warmed models/corpus, serial requests, shuffled each round; rerank_ms includes hybrid_ms',
        'complete': False,
    }
    manifest_path = args.out.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    warmed, mode = hybrid_search(questions[0]['question'], chunks, 5)
    if mode != 'hybrid':
        raise RuntimeError(f'Expected actual hybrid retrieval, got {mode}')
    rerank(questions[0]['question'], warmed)
    rows = []
    with args.out.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = None
        for repeat in range(1, args.repeats + 1):
            order = list(questions)
            random.Random(args.seed + repeat).shuffle(order)
            for index, case in enumerate(order, 1):
                started = time.perf_counter()
                lexical = search(case['question'], chunks, 5)
                lexical_ms = (time.perf_counter() - started) * 1000
                started = time.perf_counter()
                hybrid, mode = hybrid_search(case['question'], chunks, 5)
                hybrid_ms = (time.perf_counter() - started) * 1000
                if mode != 'hybrid':
                    raise RuntimeError(f"Unexpected retrieval mode for {case['id']}: {mode}")
                started = time.perf_counter()
                reranked = rerank(case['question'], hybrid)
                elapsed = (time.perf_counter() - started) * 1000
                row = dict(id=case['id'], repeat=repeat, symptom=case['symptom'], variant=case['variant'],
                           split=case['split'], mode=mode,
                           lexical=rank(lexical, case['gold']), hybrid=rank(hybrid, case['gold']),
                           rerank=rank(reranked, case['gold']), lexical_ms=round(lexical_ms, 3),
                           hybrid_ms=round(hybrid_ms, 3), rerank_ms=round(hybrid_ms + elapsed, 3))
                if writer is None:
                    writer = csv.DictWriter(stream, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row); stream.flush(); rows.append(row)
                if index % 50 == 0:
                    print(f'round {repeat}/{args.repeats}: {index}/200', flush=True)
    manifest.update(complete=True, completed_utc=datetime.now(timezone.utc).isoformat(), summary=summarize(rows))
    manifest['by_variant'] = {variant: summarize([row for row in rows if row['variant'] == variant])
                               for variant in ('standard', 'colloquial', 'long_tail')}
    manifest['by_split'] = {split: summarize([row for row in rows if row['split'] == split])
                             for split in ('development', 'regression')}
    manifest['by_symptom'] = {symptom: summarize([row for row in rows if row['symptom'] == symptom])
                               for symptom in sorted({row['symptom'] for row in rows})}
    manifest['code_unchanged_during_run'] = all(digest((ROOT / name).read_bytes()) == checksum
                                               for name, checksum in manifest['runtime_code_sha256'].items())
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    report = '# 合成检索回归：200题\n\n'
    report += f"运行完成：{manifest['completed_utc']}。源码版本：`{manifest['git_head']}`。\n\n"
    report += report_table(manifest['summary'])
    report += f'\n\n10类症状、120段合成夹具、200道不同问题。预热后顺序执行{args.repeats}轮，每组{len(rows)}次计时请求；命中率分母为200。重排使用同一混合Top-5候选池。\n'
    for variant, values in manifest['by_variant'].items():
        report += '\n## ' + variant + '\n\n' + report_table(values) + '\n'
    args.out.with_suffix('.md').write_text(report, encoding='utf-8')
    print(report, flush=True)


if __name__ == '__main__':
    main()
