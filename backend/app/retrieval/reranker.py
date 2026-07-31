"""Cross-encoder reranking with an offline-safe hybrid-order fallback."""
from __future__ import annotations

from functools import lru_cache

import structlog

logger = structlog.get_logger()


def _load_reranker(model_name: str):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name)


@lru_cache(maxsize=1)
def _get_reranker():
    from app.config import get_settings

    settings = get_settings()
    logger.info("loading_reranker", model=settings.reranker_model)
    try:
        return _load_reranker(settings.reranker_model)
    except Exception as exc:
        if not settings.reranker_fallback_enabled:
            raise
        logger.warning(
            "reranker_unavailable_using_hybrid_order",
            model=settings.reranker_model,
            error=str(exc),
        )
        return None


def _hybrid_order_fallback(candidates: list[dict], top_k: int) -> list[dict]:
    """Keep the upstream RRF or dense ranking without mutating candidates."""
    return candidates[:max(0, top_k)]


def rerank(query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
    """
    Re-rank a list of retrieved chunks using the cross-encoder.
    Each candidate must have a 'content' key.
    Returns top_k candidates sorted by cross-encoder score.
    """
    if not candidates:
        return []

    reranker = _get_reranker()
    if reranker is None:
        return _hybrid_order_fallback(candidates, top_k)

    pairs = [(query, candidate.get("content", "")) for candidate in candidates]
    try:
        scores = reranker.predict(pairs)
    except Exception as exc:
        from app.config import get_settings

        if not get_settings().reranker_fallback_enabled:
            raise
        logger.warning(
            "reranker_prediction_failed_using_hybrid_order",
            candidate_count=len(candidates),
            error=str(exc),
        )
        return _hybrid_order_fallback(candidates, top_k)

    for cand, score in zip(candidates, scores):
        cand["rerank_score"] = float(score)

    sorted_candidates = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
    return sorted_candidates[:top_k]
