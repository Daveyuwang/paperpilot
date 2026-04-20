"""
Qdrant vector database client.
Compatible with qdrant-client >= 1.9 (uses query_points API).
"""
from __future__ import annotations
import structlog
from functools import lru_cache
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    MatchAny,
    FilterSelector,
)

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


@lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    if settings.qdrant_url:
        return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    return QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)


def ensure_collection() -> None:
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    if settings.qdrant_collection not in existing:
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=VectorParams(
                size=settings.embedding_dimension,
                distance=Distance.COSINE,
            ),
        )
        logger.info("qdrant_collection_created", name=settings.qdrant_collection)


def upsert_chunks(
    paper_id: str,
    chunks: list[dict],
    embeddings: list[list[float]],
) -> None:
    """Store chunk embeddings with metadata in Qdrant."""
    ensure_collection()
    client = get_client()

    points = [
        PointStruct(
            id=chunk["id"],
            vector=vector,
            payload={
                "paper_id": paper_id,
                "chunk_id": chunk["id"],
                "content": chunk["content"],
                "section_title": chunk.get("section_title"),
                "page_number": chunk.get("page_number"),
                "content_type": chunk.get("content_type", "text"),
                "bbox": chunk.get("bbox"),
            },
        )
        for chunk, vector in zip(chunks, embeddings)
    ]

    client.upsert(collection_name=settings.qdrant_collection, points=points)


def dense_search(
    query_vector: list[float],
    paper_id: str,
    top_k: int = 10,
) -> list[dict]:
    """
    Search Qdrant for the most similar chunks for a given paper.
    Uses query_points (qdrant-client >= 1.9).
    """
    ensure_collection()
    client = get_client()

    paper_filter = Filter(
        must=[FieldCondition(key="paper_id", match=MatchValue(value=paper_id))]
    )

    response = client.query_points(
        collection_name=settings.qdrant_collection,
        query=query_vector,
        query_filter=paper_filter,
        limit=top_k,
        with_payload=True,
    )

    return [
        {
            "chunk_id": hit.payload["chunk_id"],
            "content": hit.payload["content"],
            "section_title": hit.payload.get("section_title"),
            "page_number": hit.payload.get("page_number"),
            "bbox": hit.payload.get("bbox"),
            "score": hit.score,
            "source": "dense",
        }
        for hit in response.points
    ]


def dense_search_multi(
    query_vector: list[float],
    paper_ids: list[str],
    top_k: int = 15,
) -> list[dict]:
    """Search Qdrant across multiple papers using MatchAny filter."""
    if not paper_ids:
        return []
    ensure_collection()
    client = get_client()

    paper_filter = Filter(
        must=[FieldCondition(key="paper_id", match=MatchAny(any=paper_ids))]
    )

    response = client.query_points(
        collection_name=settings.qdrant_collection,
        query=query_vector,
        query_filter=paper_filter,
        limit=top_k,
        with_payload=True,
    )

    return [
        {
            "chunk_id": hit.payload["chunk_id"],
            "content": hit.payload["content"],
            "section_title": hit.payload.get("section_title"),
            "page_number": hit.payload.get("page_number"),
            "bbox": hit.payload.get("bbox"),
            "paper_id": hit.payload.get("paper_id"),
            "score": hit.score,
            "source": "dense",
        }
        for hit in response.points
    ]


def delete_paper_chunks(paper_id: str) -> None:
    client = get_client()
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=FilterSelector(
            filter=Filter(
                must=[FieldCondition(key="paper_id", match=MatchValue(value=paper_id))]
            )
        ),
    )
