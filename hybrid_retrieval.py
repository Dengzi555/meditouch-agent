"""Local dense retrieval and rank fusion for the real evidence corpus."""
import argparse
import hashlib
import os
from pathlib import Path
import tempfile
from functools import lru_cache

from rag_pipeline import search

MODEL_NAME = 'BAAI/bge-small-zh-v1.5'
WINDOW_SIZE = 384
WINDOW_STEP = 320
CACHE_DIR = Path(__file__).parent / 'data' / 'bge-passage-cache'
PASSAGE_CACHE_VERSION = 'fastembed-0.9.1:window384:stride320:v1'


@lru_cache(maxsize=1)
def _embedding_model():
    from fastembed import TextEmbedding
    return TextEmbedding(MODEL_NAME, threads=2, local_files_only=True)


@lru_cache(maxsize=256)
def _passage_vectors(text):
    import numpy as np

    # Index long evidence in overlapping windows. Citations still point to the
    # complete evidence unit, including qualifications outside the best window.
    windows = [text[start:start + WINDOW_SIZE] for start in range(0, len(text), WINDOW_STEP)]
    key = hashlib.sha256(f'{MODEL_NAME}:{PASSAGE_CACHE_VERSION}:{text}'.encode()).hexdigest()
    path = CACHE_DIR / f'{key}.npy'
    try:
        cached = np.load(path, allow_pickle=False)
        if (cached.shape == (len(windows), 512) and cached.dtype == np.float32
                and np.isfinite(cached).all() and np.all(np.linalg.norm(cached, axis=1) > 0)):
            return cached
    except (OSError, ValueError, EOFError):
        pass
    vectors = np.asarray(list(_embedding_model().passage_embed(windows)), dtype=np.float32)
    temporary = None
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=CACHE_DIR, suffix='.npy', delete=False) as stream:
            temporary = Path(stream.name)
            np.save(stream, vectors, allow_pickle=False)
        os.replace(temporary, path)
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return vectors


def _dense_matches(question, items, limit):
    from numpy.linalg import norm

    query = next(_embedding_model().query_embed(question))
    query_norm = norm(query)
    ranked = []
    for item in items:
        text = item['text'].strip()
        if text:
            vectors = _passage_vectors(text)
            similarities = (vectors @ query) / (norm(vectors, axis=1) * query_norm)
            start = int(similarities.argmax()) * WINDOW_STEP
            ranked.append((float(similarities[start // WINDOW_STEP]), item['id'],
                           {'start': start, 'end': min(start + WINDOW_SIZE, len(text))}))
    return {item_id: window for _, item_id, window in sorted(ranked, key=lambda row: (-row[0], row[1]))[:limit]}


def hybrid_search(question, items, k):
    if not items:
        return [], 'empty'
    limit = max(8, 2 * k)
    lexical = search(question, items, limit)
    try:
        dense = _dense_matches(question, items, limit)
    except Exception:
        # The runtime can still answer via BM25 when the optional ONNX model
        # or its local cache is unavailable. Surface the mode in tool coverage.
        return lexical[:k], 'bm25_fallback'
    if not dense:
        return lexical[:k], 'bm25'

    scores = {}
    for ids, weight in (([item['id'] for item in lexical], 2), (dense, 1)):
        for rank, item_id in enumerate(ids, start=1):
            scores[item_id] = scores.get(item_id, 0) + weight / (60 + rank)
    by_id = {item['id']: item for item in items}
    ranked = sorted(scores, key=lambda item_id: (-scores[item_id], item_id))
    return [{**by_id[item_id], 'score': round(scores[item_id], 6),
             **({'retrieval_window': dense[item_id]} if item_id in dense else {})}
            for item_id in ranked[:k]], 'hybrid'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Prepare or check the local BGE model')
    parser.add_argument('--check', action='store_true', help='Check the local model without downloading')
    options = parser.parse_args()
    if options.check:
        model = _embedding_model()
    else:
        from fastembed import TextEmbedding
        model = TextEmbedding(MODEL_NAME, threads=2)
    query = next(model.query_embed('检索'))
    print(f'{MODEL_NAME}: {query.shape[0]} dimensions ready; passage cache: {CACHE_DIR}')
