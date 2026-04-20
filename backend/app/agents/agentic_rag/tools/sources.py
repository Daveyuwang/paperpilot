"""
Source discovery and management tools for the Console agent.
Wraps the existing OpenAlex/arXiv discovery endpoint.
"""
from __future__ import annotations

import httpx
import structlog
from langchain_core.tools import tool

logger = structlog.get_logger()


@tool
async def discover_sources(query: str, max_results: int = 10, recency_years: int | None = None) -> list[dict]:
    """Search for academic papers on OpenAlex, arXiv, and Semantic Scholar.

    Use this tool when the user wants to find new papers on a topic, expand their
    reading list, or discover related work. Returns paper metadata including title,
    authors, year, abstract, and citation count.

    Args:
        query: The search query describing the topic or keywords.
        max_results: Maximum number of results to return (default 10, max 20).
        recency_years: If set, only return papers from the last N years (e.g., 3 for last 3 years).
    """
    from app.api.sources import (
        _parse_openalex,
        _parse_arxiv,
        _dedupe,
        OPENALEX_API,
        ARXIV_API,
    )
    from app.config import get_settings
    from datetime import datetime

    settings = get_settings()
    max_results = min(max_results, 20)
    all_results = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        # OpenAlex
        try:
            resp = await client.get(OPENALEX_API, params={
                "search": query,
                "per_page": 15,
                "sort": "relevance_score:desc",
                "select": "id,title,authorships,publication_year,doi,abstract_inverted_index,cited_by_count,primary_location",
            })
            if resp.status_code == 200:
                all_results.extend(_parse_openalex(resp.json().get("results", [])))
        except Exception as exc:
            logger.warning("discover_openalex_failed", error=str(exc))

        # arXiv
        try:
            resp = await client.get(ARXIV_API, params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": 10,
                "sortBy": "relevance",
                "sortOrder": "descending",
            })
            if resp.status_code == 200:
                all_results.extend(_parse_arxiv(resp.text))
        except Exception as exc:
            logger.warning("discover_arxiv_failed", error=str(exc))

        # Semantic Scholar
        try:
            headers = {}
            if settings.semantic_scholar_api_key:
                headers["x-api-key"] = settings.semantic_scholar_api_key
            resp = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={
                    "query": query,
                    "limit": 10,
                    "fields": "title,authors,year,citationCount,externalIds,abstract",
                },
                headers=headers,
            )
            if resp.status_code == 200:
                from app.api.sources import DiscoveredSource
                for p in resp.json().get("data", []):
                    title = (p.get("title") or "").strip()
                    if not title:
                        continue
                    ext_ids = p.get("externalIds") or {}
                    all_results.append(DiscoveredSource(
                        external_id=ext_ids.get("DOI") or ext_ids.get("ArXiv") or p.get("paperId", ""),
                        provider="semantic_scholar",
                        title=title,
                        authors=[a.get("name", "") for a in (p.get("authors") or [])[:5]],
                        year=p.get("year"),
                        doi=ext_ids.get("DOI"),
                        arxiv_id=ext_ids.get("ArXiv"),
                        abstract=(p.get("abstract") or "")[:500],
                        citation_count=p.get("citationCount"),
                        url=f"https://www.semanticscholar.org/paper/{p.get('paperId', '')}",
                    ))
        except Exception as exc:
            logger.warning("discover_semantic_scholar_failed", error=str(exc))

    deduped = _dedupe(all_results)

    # Apply recency filter
    if recency_years:
        cutoff_year = datetime.now().year - recency_years
        deduped = [s for s in deduped if s.year and s.year >= cutoff_year]

    return [
        {
            "external_id": s.external_id,
            "provider": s.provider,
            "title": s.title,
            "authors": s.authors[:3],
            "year": s.year,
            "abstract": (s.abstract or "")[:300],
            "citation_count": s.citation_count,
            "url": s.url,
        }
        for s in deduped[:max_results]
    ]


@tool
async def manage_sources(action: str, workspace_id: str, source_id: str = "") -> dict:
    """View or manage sources in the workspace.

    Use this tool when the user wants to see their current sources, or add/remove
    a specific source. Note: source persistence is managed by the frontend state.
    This tool returns the current papers in the workspace as the source list.

    Args:
        action: One of 'list', 'include', 'discard'.
        workspace_id: The workspace ID.
        source_id: The source/paper ID (required for 'include' and 'discard' actions).
    """
    if action == "list":
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy import select
        from app.models.orm import Paper, PaperStatus
        from app.config import get_settings

        engine = create_async_engine(get_settings().database_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as db:
            result = await db.execute(
                select(Paper)
                .where(Paper.workspace_id == workspace_id)
                .order_by(Paper.created_at.desc())
            )
            papers = list(result.scalars())

        return {
            "action": "list",
            "sources": [
                {
                    "id": p.id,
                    "title": p.title or p.filename,
                    "status": p.status.value,
                    "authors": (p.authors or [])[:3],
                }
                for p in papers
            ],
        }

    if action in ("include", "discard") and not source_id:
        return {"error": f"source_id is required for '{action}' action."}

    # include/discard are frontend-managed operations — return instruction for frontend
    return {
        "action": action,
        "source_id": source_id,
        "instruction": f"Frontend should {action} source {source_id}.",
    }
