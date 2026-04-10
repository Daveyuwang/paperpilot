"""
Cross-encoder re-ranker using ms-marco-MiniLM for final result ordering.
"""
from __future__ import annotations
import structlog
from functools import lru_cache

logger = structlog.get_logger()


@lru_cache(maxsize=1)
def _get_reranker():
    from sentence_transformers import CrossEncoder
    from app.config import get_settings
    settings = get_settings()
    logger.info("loading_reranker", model=settings.reranker_model)
    return CrossEncoder(settings.reranker_model)


def rerank(query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
    """
    Re-rank a list of retrieved chunks using the cross-encoder.
    Each candidate must have a 'content' key.
    Returns top_k candidates sorted by cross-encoder score.
    """
    if not candidates:
        return []

    reranker = _get_reranker()
    pairs = [(query, c["content"]) for c in candidates]
    scores = reranker.predict(pairs)

    for cand, score in zip(candidates, scores):
        cand["rerank_score"] = float(score)

    sorted_candidates = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
    return sorted_candidates[:top_k]
