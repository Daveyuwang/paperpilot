"""
Deliverable analysis tools — coherence checking and transition suggestions.
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
async def check_deliverable_coherence(sections: list[dict]) -> dict:
    """Check a deliverable for cross-section coherence issues.

    Analyzes all sections together to find contradictions, gaps, redundancy,
    or logical flow problems. Use when the user asks to review their document
    or before finalizing a deliverable.

    Args:
        sections: List of section dicts with 'title' and 'content' keys.
    """
    if not sections or len(sections) < 2:
        return {"issues": [], "summary": "Need at least 2 sections to check coherence."}

    llm = ChatAnthropic(
        model=get_model_name(),
        api_key=get_anthropic_api_key(),
        max_tokens=1024,
        temperature=0.1,
    )

    section_block = "\n\n".join(
        f"## {s.get('title', f'Section {i+1}')}\n{(s.get('content') or '')[:500]}"
        for i, s in enumerate(sections)
    )

    system = (
        "You are an academic writing reviewer. Analyze the document sections for coherence issues. "
        "Return JSON only:\n"
        "{\"issues\": [{\"type\": \"contradiction|gap|redundancy|flow\", \"sections\": [<indices>], "
        "\"description\": \"<1 sentence>\"}], \"summary\": \"<overall assessment in 1 sentence>\"}"
    )

    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=f"Document sections:\n{section_block}"),
    ])

    try:
        return json.loads(response.content)
    except (json.JSONDecodeError, KeyError):
        return {"issues": [], "summary": response.content[:300]}


@tool
async def suggest_section_transitions(
    section_before: dict,
    section_after: dict,
) -> str:
    """Suggest transition text between two adjacent deliverable sections.

    Use when the user wants to improve flow between sections or when
    check_deliverable_coherence identifies a flow issue.

    Args:
        section_before: Dict with 'title' and 'content' of the preceding section.
        section_after: Dict with 'title' and 'content' of the following section.
    """
    llm = ChatAnthropic(
        model=get_model_name(),
        api_key=get_anthropic_api_key(),
        max_tokens=300,
        temperature=0.3,
    )

    prompt = (
        f"Write a 1-2 sentence transition between these sections:\n\n"
        f"END OF: {section_before.get('title', 'Previous')}\n"
        f"...{(section_before.get('content') or '')[-200:]}\n\n"
        f"START OF: {section_after.get('title', 'Next')}\n"
        f"{(section_after.get('content') or '')[:200]}...\n\n"
        f"Provide only the transition sentence(s), no explanation."
    )

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    return response.content
