"""
Academic knowledge tools — Semantic Scholar API + Tavily web search.
"""
from __future__ import annotations

import httpx
import structlog
from langchain_core.tools import tool

from app.config import get_settings

logger = structlog.get_logger()

SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1"


@tool
async def search_academic_papers(query: str, max_results: int = 5) -> list[dict]:
    """Search Semantic Scholar for academic papers related to a concept or topic.

    Use this when the user asks about related work, prior art, or wants to understand
    how a concept connects to the broader literature beyond their uploaded papers.

    Args:
        query: Search query (e.g., 'transformer attention mechanisms', 'BERT pre-training').
        max_results: Number of results (default 5, max 10).
    """
    settings = get_settings()
    max_results = min(max_results, 10)
    headers = {}
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key

    async def _do_search() -> list[dict]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{SEMANTIC_SCHOLAR_API}/paper/search",
                params={
                    "query": query,
                    "limit": max_results,
                    "fields": "title,abstract,year,citationCount,authors,url,externalIds",
                },
                headers=headers,
            )
            if resp.status_code != 200:
                logger.warning("semantic_scholar_search_failed", status=resp.status_code)
                return []

            data = resp.json().get("data", [])
            return [
                {
                    "paper_id": p.get("paperId", ""),
                    "title": p.get("title", ""),
                    "abstract": (p.get("abstract") or "")[:300],
                    "year": p.get("year"),
                    "citation_count": p.get("citationCount", 0),
                    "authors": [a.get("name", "") for a in (p.get("authors") or [])[:3]],
                    "url": p.get("url", ""),
                    "arxiv_id": (p.get("externalIds") or {}).get("ArXiv"),
                }
                for p in data
            ]

    try:
        return await _do_search()
    except Exception as exc:
        logger.warning("semantic_scholar_search_error", error=str(exc))
        return []


@tool
async def get_citation_context(semantic_scholar_id: str) -> dict:
    """Get citation and reference context for a paper from Semantic Scholar.

    Use this to understand a paper's influence (who cites it) and foundations
    (what it builds on). Helps answer 'what came after this?' or 'what inspired this?'.

    Args:
        semantic_scholar_id: The Semantic Scholar paper ID.
    """
    settings = get_settings()
    headers = {}
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key

    result = {"citations": [], "references": []}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Citations (papers that cite this one)
            resp = await client.get(
                f"{SEMANTIC_SCHOLAR_API}/paper/{semantic_scholar_id}/citations",
                params={"fields": "title,year,citationCount,authors", "limit": 5},
                headers=headers,
            )
            if resp.status_code == 200:
                for item in resp.json().get("data", []):
                    cp = item.get("citingPaper", {})
                    result["citations"].append({
                        "title": cp.get("title", ""),
                        "year": cp.get("year"),
                        "citation_count": cp.get("citationCount", 0),
                        "authors": [a.get("name", "") for a in (cp.get("authors") or [])[:2]],
                    })

            # References (papers this one cites)
            resp = await client.get(
                f"{SEMANTIC_SCHOLAR_API}/paper/{semantic_scholar_id}/references",
                params={"fields": "title,year,citationCount,authors", "limit": 5},
                headers=headers,
            )
            if resp.status_code == 200:
                for item in resp.json().get("data", []):
                    cp = item.get("citedPaper", {})
                    result["references"].append({
                        "title": cp.get("title", ""),
                        "year": cp.get("year"),
                        "citation_count": cp.get("citationCount", 0),
                        "authors": [a.get("name", "") for a in (cp.get("authors") or [])[:2]],
                    })
    except Exception as exc:
        logger.warning("citation_context_error", error=str(exc))

    return result


@tool
async def web_search(query: str, max_results: int = 3) -> list[dict]:
    """Search the web for current information about a topic using Tavily.

    Use this when the user asks about very recent developments, blog posts,
    tutorials, or information not typically found in academic databases.
    Gated by the web_search_enabled feature flag.

    Args:
        query: The web search query.
        max_results: Number of results (default 3, max 5).
    """
    settings = get_settings()
    if not settings.web_search_enabled:
        return [{"error": "Web search is disabled by configuration."}]

    from app.deep_research.tools.search import tavily_search

    max_results = min(max_results, 5)
    results = await tavily_search([query], max_results_per_query=max_results)
    return results[:max_results]
