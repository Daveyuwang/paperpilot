"""
Router node — classifies user intent to route to the correct subgraph.
"""
from __future__ import annotations

import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from app.agents.agentic_rag.config import get_model_name, get_anthropic_api_key
from app.agents.agentic_rag.prompts import ROUTER_SYSTEM


async def router_node(state: dict) -> dict:
    """Classify user intent and route to paper_qa, console, or direct_response."""
    messages = state["messages"]
    question = ""
    for msg in messages:
        if hasattr(msg, "type") and msg.type == "human":
            question = msg.content
            break

    has_paper = bool(state.get("paper_id") or state.get("active_paper_id"))
    is_console = not has_paper

    system_prompt = ROUTER_SYSTEM.format(
        has_paper=has_paper,
        is_console=is_console,
    )

    llm = ChatAnthropic(
        model=get_model_name(),
        api_key=get_anthropic_api_key(),
        max_tokens=100,
        temperature=0.1,
    )

    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=question),
    ])

    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        result = json.loads(raw)
        route = result.get("route", "paper_qa" if has_paper else "console")
    except json.JSONDecodeError:
        route = "paper_qa" if has_paper else "console"

    return {"route": route}
