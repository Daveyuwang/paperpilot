from __future__ import annotations

import asyncio
import structlog
import httpx
import trafilatura

from app.deep_research.config import SEARCH_CONCURRENCY, MAX_PAGE_CHARS

logger = structlog.get_logger()

_semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)


async def fetch_page_content(url: str, timeout: float = 15.0) -> str:
    async with _semaphore:
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": "PaperPilot-Research/1.0"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text

            text = await asyncio.to_thread(
                trafilatura.extract, html, include_comments=False, include_tables=True
            )
            if not text:
                return ""
            return text[:MAX_PAGE_CHARS]
        except Exception as exc:
            logger.warning("fetch_page_failed", url=url, error=str(exc))
            return ""


async def fetch_pages(urls: list[str]) -> list[tuple[str, str]]:
    tasks = [fetch_page_content(url) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[tuple[str, str]] = []
    for url, result in zip(urls, results):
        if isinstance(result, Exception):
            logger.warning("fetch_page_exception", url=url, error=str(result))
            out.append((url, ""))
        else:
            out.append((url, result))
    return out
