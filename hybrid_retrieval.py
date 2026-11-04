"""Local dense retrieval and rank fusion for the real evidence corpus."""
from functools import lru_cache

from rag_pipeline import search

MODEL_NAME = 'BAAI/bge-small-zh-v1.5'


@lru_cache(maxsize=1)
def _embedding_model():
    from fastembed import TextEmbedding
    return TextEmbedding(MODEL_NAME, threads=2, local_files_only=True)


@lru_cache(maxsize=256)
def _passage_vectors(text):
    # Index long evidence in overlapping windows. Citations still point to the
    # complete evidence unit, including qualifications outside the best window.
    windows = [text[start:start + 384] for start in range(0, len(text), 320)]
    return tuple(_embedding_model().passage_embed(windows))


def _dense_ids(question, items, limit):
    from numpy.linalg import norm

    query = next(_embedding_model().query_embed(question))
    query_norm = norm(query)
    ranked = []
    for item in items:
        text = item['text'].strip()
        if text:
            similarity = max(float((vector @ query) / (norm(vector) * query_norm))
                             for vector in _passage_vectors(text))
            ranked.append((similarity, item['id']))
    return [item_id for _, item_id in sorted(ranked, key=lambda row: (-row[0], row[1]))[:limit]]


def hybrid_search(question, items, k):
    if not items:
        return [], 'empty'
    limit = max(8, 2 * k)
    lexical = search(question, items, limit)
    try:
        dense = _dense_ids(question, items, limit)
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
    return [{**by_id[item_id], 'score': round(scores[item_id], 6)} for item_id in ranked[:k]], 'hybrid'


if __name__ == '__main__':
    from fastembed import TextEmbedding
    model = TextEmbedding(MODEL_NAME, threads=2)
    print(f'{MODEL_NAME}: {next(model.query_embed("检索")).shape[0]} dimensions ready')
