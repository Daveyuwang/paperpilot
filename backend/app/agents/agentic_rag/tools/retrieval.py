"""
Retrieval tools for the agentic RAG system.
Wraps the existing hybrid retrieval pipeline as LangChain @tool functions.
"""
from __future__ import annotations

from langchain_core.tools import tool

from app.retrieval.hybrid import hybrid_retrieve as _hybrid_retrieve
from app.agents.agentic_rag.config import RETRIEVAL_TOP_K


@tool
async def retrieve_from_paper(query: str, paper_id: str) -> list[dict]:
    """Retrieve relevant passages from a specific paper using hybrid search (dense + sparse + reranking).

    Use this tool when you need evidence from the paper to answer a question.
    Returns ranked chunks with content, section titles, page numbers, and bounding boxes.

    Args:
        query: The search query — be specific and use paper terminology for best results.
        paper_id: The paper ID to search within.
    """
    chunks = await _hybrid_retrieve(
        query=query,
        paper_id=paper_id,
        top_k=RETRIEVAL_TOP_K,
    )
    chunks = await _fill_missing_content(chunks, paper_id)
    return [
        {
            "chunk_id": c.get("chunk_id"),
            "content": (c.get("content") or "")[:800],
            "section_title": c.get("section_title"),
            "page_number": c.get("page_number"),
            "bbox": c.get("bbox"),
            "score": round(c.get("score", 0), 4),
        }
        for c in chunks
    ]


@tool
async def search_workspace_sources(query: str, workspace_id: str) -> list[dict]:
    """Search across ALL papers in a workspace to find relevant passages from multiple sources.

    Use this tool for comparative questions, cross-paper searches, or when the user asks
    'which of my papers discusses X'. Returns chunks with source paper attribution.

    Args:
        query: The search query.
        workspace_id: The workspace to search across.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select
    from app.models.orm import Paper, PaperStatus
    from app.config import get_settings
    from app.retrieval.qdrant_client import dense_search_multi
    from app.retrieval.reranker import rerank

    engine = create_async_engine(get_settings().database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        result = await db.execute(
            select(Paper)
            .where(Paper.workspace_id == workspace_id, Paper.status == PaperStatus.ready)
        )
        papers = list(result.scalars())

    if not papers:
        return []

    paper_ids = [p.id for p in papers]
    paper_map = {p.id: (p.title or p.filename) for p in papers}

    # Embed query
    from app.retrieval.embedder import embed_query
    query_vector = embed_query(query)

    # Single multi-paper vector search
    raw_chunks = dense_search_multi(query_vector, paper_ids, top_k=15)
    raw_chunks = await _fill_missing_content(raw_chunks, paper_ids[0])

    # Rerank
    if raw_chunks:
        reranked = rerank(query, raw_chunks, top_k=10)
    else:
        reranked = []

    return [
        {
            "chunk_id": c.get("chunk_id"),
            "content": (c.get("content") or "")[:600],
            "section_title": c.get("section_title"),
            "page_number": c.get("page_number"),
            "score": round(c.get("score", 0), 4),
            "paper_id": c.get("paper_id"),
            "paper_title": paper_map.get(c.get("paper_id", ""), "Unknown"),
        }
        for c in reranked[:10]
    ]


async def _fill_missing_content(chunks: list[dict], paper_id: str) -> list[dict]:
    """Fill in content for chunks that only have IDs (from Qdrant)."""
    missing_ids = [c["chunk_id"] for c in chunks if not c.get("content")]
    if not missing_ids:
        return chunks

    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select
    from app.models.orm import Chunk
    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        result = await db.execute(select(Chunk).where(Chunk.id.in_(missing_ids)))
        chunk_map = {c.id: c for c in result.scalars()}

    for item in chunks:
        if not item.get("content") and item["chunk_id"] in chunk_map:
            c = chunk_map[item["chunk_id"]]
            item["content"] = c.content
            item["section_title"] = c.section_title
            item["page_number"] = c.page_number
            item["bbox"] = c.bbox
    return chunks
