"""
External knowledge tools for the agentic RAG system.
Wikipedia + Tavily fallback for background knowledge.
"""
from __future__ import annotations

import httpx
import structlog
from langchain_core.tools import tool

from app.config import get_settings

logger = structlog.get_logger()


@tool
async def fetch_external_background(concept: str) -> str:
    """Fetch brief background knowledge about a concept or term from Wikipedia, with web search fallback.

    Use this tool when the paper references a foundational concept, prior work,
    or technical term that a non-specialist reader might not know. Do NOT use
    for concepts that are well-explained within the paper itself.

    Args:
        concept: The concept or term to look up (e.g., 'attention mechanism', 'BERT', 'cross-entropy loss').
    """
    # ── Try Wikipedia first (with circuit breaker) ────────────────────────
    async def _wikipedia_fetch() -> str | None:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                "https://en.wikipedia.org/api/rest_v1/page/summary/"
                + concept.replace(" ", "_")
            )
            if resp.status_code == 200:
                extract = resp.json().get("extract", "")
                if extract and len(extract) > 50:
                    return f"Wikipedia — {concept}: {extract[:700]}"
        return None

    wiki_result: str | None = None
    try:
        from app.circuit_breaker import wikipedia_breaker, PYBREAKER_AVAILABLE

        if PYBREAKER_AVAILABLE:
            import pybreaker

            try:
                wiki_result = await wikipedia_breaker.call_async(_wikipedia_fetch)
            except pybreaker.CircuitBreakerError:
                logger.warning("wikipedia_circuit_open", concept=concept)
        else:
            wiki_result = await _wikipedia_fetch()
    except ImportError:
        try:
            wiki_result = await _wikipedia_fetch()
        except Exception as exc:
            logger.warning("wikipedia_fetch_failed", concept=concept, error=str(exc))
    except Exception as exc:
        logger.warning("wikipedia_fetch_failed", concept=concept, error=str(exc))

    if wiki_result:
        return wiki_result

    # ── Fallback to Tavily web search ─────────────────────────────────────
    settings = get_settings()
    if settings.web_search_enabled and settings.tavily_api_key:
        try:
            from app.deep_research.tools.search import tavily_search
            results = await tavily_search([f"{concept} explanation definition"], max_results_per_query=2)
            if results:
                snippets = " | ".join(r.get("snippet", "")[:200] for r in results[:2])
                return f"Web — {concept}: {snippets[:700]}"
        except Exception as exc:
            logger.warning("tavily_fallback_failed", concept=concept, error=str(exc))

    return f"No external background found for '{concept}'."
