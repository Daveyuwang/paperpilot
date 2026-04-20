"""
Source analysis tools — LLM-powered relevance ranking of discovered sources.
"""
from __future__ import annotations

import json
import structlog
from langchain_core.tools import tool
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from app.agents.agentic_rag.config import get_model_name, get_anthropic_api_key

logger = structlog.get_logger()


@tool
async def analyze_and_rank_sources(sources: list[dict], research_question: str) -> list[dict]:
    """Analyze and rank discovered sources by relevance to a research question.

    Use this after discover_sources to help the user decide which papers are most
    worth reading. The LLM evaluates each source's abstract against the research question.

    Args:
        sources: List of source dicts (from discover_sources) with title, abstract, year, citation_count.
        research_question: The user's research question or goal to rank against.
    """
    if not sources:
        return []

    llm = ChatAnthropic(
        model=get_model_name(),
        api_key=get_anthropic_api_key(),
        max_tokens=1024,
        temperature=0.1,
    )

    source_block = "\n".join(
        f"[{i}] {s.get('title', '')} ({s.get('year', '?')}) — {s.get('abstract', '')[:200]}"
        for i, s in enumerate(sources[:10])
    )

    system = (
        "You are a research librarian. Rank the given papers by relevance to the research question. "
        "Return JSON only: {\"ranked\": [{\"index\": <int>, \"score\": <0-10>, \"reason\": \"<1 sentence>\"}]}"
    )

    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=f"Research question: {research_question}\n\nSources:\n{source_block}"),
    ])

    try:
        parsed = json.loads(response.content)
        ranked = parsed.get("ranked", [])
        result = []
        for item in sorted(ranked, key=lambda x: x.get("score", 0), reverse=True):
            idx = item.get("index", 0)
            if 0 <= idx < len(sources):
                entry = {**sources[idx], "relevance_score": item.get("score", 0), "reason": item.get("reason", "")}
                result.append(entry)
        return result
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("source_analysis_parse_failed", error=str(exc))
        return sources
