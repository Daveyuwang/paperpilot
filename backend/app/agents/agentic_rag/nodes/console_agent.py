"""
Console agent node — LLM with bound workspace tools.
"""
from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from langgraph.prebuilt import ToolNode

from app.agents.agentic_rag.config import get_model_name, get_anthropic_api_key, MAX_TOOL_CALLS_CONSOLE
from app.agents.agentic_rag.prompts import CONSOLE_AGENT_SYSTEM
from app.agents.agentic_rag.tools.workspace import get_workspace_overview, navigate_to
from app.agents.agentic_rag.tools.sources import discover_sources, manage_sources
from app.agents.agentic_rag.tools.deliverables import list_deliverables, read_deliverable_section, draft_deliverable_section
from app.agents.agentic_rag.tools.agenda import get_agenda, update_agenda
from app.agents.agentic_rag.tools.retrieval import search_workspace_sources
from app.agents.agentic_rag.tools.session import get_session_context
from app.agents.agentic_rag.tools.source_analysis import analyze_and_rank_sources
from app.agents.agentic_rag.tools.paper_fetch import fetch_paper_fulltext
from app.agents.agentic_rag.tools.deliverable_analysis import check_deliverable_coherence, suggest_section_transitions

CONSOLE_TOOLS = [
    get_workspace_overview,
    navigate_to,
    discover_sources,
    manage_sources,
    list_deliverables,
    read_deliverable_section,
    draft_deliverable_section,
    get_agenda,
    update_agenda,
    search_workspace_sources,
    get_session_context,
    analyze_and_rank_sources,
    fetch_paper_fulltext,
    check_deliverable_coherence,
    suggest_section_transitions,
]


def get_console_llm():
    return ChatAnthropic(
        model=get_model_name(),
        api_key=get_anthropic_api_key(),
        max_tokens=1500,
        temperature=0.3,
    ).bind_tools(CONSOLE_TOOLS)


async def console_agent_node(state: dict) -> dict:
    """Console agent: LLM decides which workspace tools to call."""
    llm = get_console_llm()

    system_prompt = CONSOLE_AGENT_SYSTEM.format(
        max_tool_calls=MAX_TOOL_CALLS_CONSOLE,
        workspace_title=state.get("workspace_title", "My Workspace"),
        paper_count=state.get("paper_count", 0),
        active_paper_name=state.get("active_paper_name", "None"),
        workspace_snapshot=state.get("workspace_snapshot", ""),
    )

    messages = [SystemMessage(content=system_prompt)] + state["messages"]
    response = await llm.ainvoke(messages)

    new_count = state.get("tool_call_count", 0)
    if response.tool_calls:
        new_count += len(response.tool_calls)

    return {"messages": [response], "tool_call_count": new_count}


console_tool_node = ToolNode(CONSOLE_TOOLS)
