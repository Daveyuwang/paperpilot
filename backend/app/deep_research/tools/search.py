from __future__ import annotations

import asyncio
import structlog
from tavily import AsyncTavilyClient

from app.config import get_settings
from app.deep_research.config import SEARCH_CONCURRENCY

logger = structlog.get_logger()

_semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)


async def tavily_search(
    queries: list[str],
    max_results_per_query: int = 5,
) -> list[dict]:
    api_key = get_settings().tavily_api_key
    if not api_key:
        logger.warning("tavily_api_key_not_set")
        return []

    client = AsyncTavilyClient(api_key=api_key)
    seen_urls: set[str] = set()
    results: list[dict] = []

    async def _single_search(query: str) -> list[dict]:
        async with _semaphore:
            try:
                resp = await client.search(
                    query=query,
                    max_results=max_results_per_query,
                    include_raw_content=False,
                )
                return resp.get("results", [])
            except Exception as exc:
                logger.warning("tavily_search_failed", query=query, error=str(exc))
                return []

    # ── Circuit-breaker wrapper ───────────────────────────────────────────
    try:
        from app.circuit_breaker import tavily_breaker, PYBREAKER_AVAILABLE

        if PYBREAKER_AVAILABLE:
            import pybreaker

            async def _guarded_gather() -> list[list[dict]]:
                return await tavily_breaker.call_async(
                    asyncio.gather, *[_single_search(q) for q in queries]
                )

            try:
                all_raw = await _guarded_gather()
            except pybreaker.CircuitBreakerError:
                logger.warning("tavily_circuit_open")
                return []
        else:
            all_raw = await asyncio.gather(*[_single_search(q) for q in queries])
    except ImportError:
        all_raw = await asyncio.gather(*[_single_search(q) for q in queries])

    for batch in all_raw:
        for item in batch:
            url = item.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append({
                    "url": url,
                    "title": item.get("title", ""),
                    "snippet": item.get("content", ""),
                    "score": item.get("score", 0.0),
                })

    results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return results
