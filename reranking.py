"""Optional local cross-encoder for the small post-RRF candidate pool."""
from functools import lru_cache
from math import isfinite
from pathlib import Path

MODEL_NAME = 'BAAI/bge-reranker-base'
CACHE_DIR = Path(__file__).parent / 'data' / 'reranker-cache'


@lru_cache(maxsize=1)
def load_model():
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(MODEL_NAME, cache_dir=str(CACHE_DIR), threads=2, local_files_only=True)


def scored_candidates(question, candidates):
    if not candidates:
        return []
    passages = []
    for item in candidates:
        text = item['text'].strip()
        window = item.get('retrieval_window')
        if window:
            text = text[window['start']:window['end']]
        title = item.get('source', {}).get('title', '')
        passages.append(f'{title}\n{text}' if title else text)
    scores = list(load_model().rerank(question, passages, batch_size=5))
    if len(scores) != len(candidates) or not all(isfinite(score) for score in scores):
        raise ValueError('Invalid reranker scores')
    return sorted(zip(scores, candidates), key=lambda pair: -pair[0])


def rerank(question, candidates, min_score=None):
    if min_score is None and len(candidates) < 2:
        return candidates
    return [item for score, item in scored_candidates(question, candidates)
            if min_score is None or score >= min_score]


if __name__ == '__main__':
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    model = TextCrossEncoder(MODEL_NAME, cache_dir=str(CACHE_DIR), threads=2)
    print(list(model.rerank('普通感冒怎么护理？', ['普通感冒护理', '高血压饮食'])))
