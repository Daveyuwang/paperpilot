"""
Hybrid retrieval: fuses dense (Qdrant) + sparse (BM25) results,
then re-ranks with the cross-encoder.
"""
from __future__ import annotations
import asyncio
import structlog

from app.retrieval.embedder import embed_query
from app.retrieval.qdrant_client import dense_search
from app.retrieval.bm25_index import bm25_search, deserialize_bm25
from app.retrieval.reranker import rerank
from app.db.redis_client import get_redis

logger = structlog.get_logger()

DENSE_TOP_K = 15
BM25_TOP_K = 15
RERANK_TOP_K = 5


async def hybrid_retrieve(
    query: str,
    paper_id: str,
    anchor_sections: list[str] | None = None,
    top_k: int = RERANK_TOP_K,
) -> list[dict]:
    """
    Full hybrid retrieval pipeline:
    1. Dense search via Qdrant
    2. BM25 sparse search from Redis
    3. Reciprocal rank fusion
    4. Cross-encoder re-ranking
    Returns top_k chunks with metadata.
    """
    # Dense retrieval
    query_vec = embed_query(query)
    dense_results = dense_search(query_vec, paper_id, top_k=DENSE_TOP_K)

    # BM25 retrieval
    bm25_results = await _bm25_retrieve(query, paper_id, top_k=BM25_TOP_K)

    # Section-filter boost: if anchor sections provided, prefer matching chunks
    if anchor_sections:
        dense_results = _boost_section_matches(dense_results, anchor_sections)
        bm25_results = _boost_section_matches(bm25_results, anchor_sections)

    # Reciprocal Rank Fusion
    fused = _rrf_fusion(dense_results, bm25_results)

    # Re-rank
    reranked = rerank(query, fused, top_k=top_k)
    return reranked


async def _bm25_retrieve(query: str, paper_id: str, top_k: int) -> list[dict]:
    """Load BM25 index from Redis and run sparse retrieval."""
    r = get_redis()
    # Use raw bytes client for pickle
    import redis.asyncio as aioredis
    from app.config import get_settings
    raw_client = aioredis.Redis.from_url(get_settings().redis_url, decode_responses=False)
    data = await raw_client.get(f"bm25:{paper_id}")
    if not data:
        logger.warning("bm25_index_not_found", paper_id=paper_id)
        return []

    bm25, chunk_ids = deserialize_bm25(data)
    # We need chunk content to return meaningful results; store in payload as well
    results = bm25_search(bm25, chunk_ids, [""] * len(chunk_ids), query, top_k=top_k)
    return results


def _boost_section_matches(results: list[dict], anchor_sections: list[str]) -> list[dict]:
    """Apply a score multiplier to results whose section_title matches anchor_sections."""
    anchor_lower = {s.lower() for s in anchor_sections}
    for r in results:
        section = (r.get("section_title") or "").lower()
        if any(a in section or section in a for a in anchor_lower):
            r["score"] = r.get("score", 0) * 1.3
    return results


def _rrf_fusion(
    dense: list[dict],
    sparse: list[dict],
    k: int = 60,
) -> list[dict]:
    """
    Reciprocal Rank Fusion to merge dense and sparse result lists.
    Returns deduplicated list ordered by combined RRF score.
    """
    scores: dict[str, float] = {}
    by_id: dict[str, dict] = {}

    for rank, item in enumerate(dense):
        cid = item["chunk_id"]
        scores[cid] = scores.get(cid, 0) + 1 / (k + rank + 1)
        by_id[cid] = item

    for rank, item in enumerate(sparse):
        cid = item["chunk_id"]
        scores[cid] = scores.get(cid, 0) + 1 / (k + rank + 1)
        if cid not in by_id:
            by_id[cid] = item

    sorted_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [by_id[cid] for cid in sorted_ids]
