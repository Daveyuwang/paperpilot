"""
Paper QA agent node — LLM with bound tools that decides what to retrieve.
"""
from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage
from langgraph.prebuilt import ToolNode

from app.agents.agentic_rag.config import get_model_name, get_anthropic_api_key, MAX_TOOL_CALLS_PAPER_QA
from app.agents.agentic_rag.prompts import PAPER_QA_AGENT_SYSTEM
from app.agents.agentic_rag.tools.retrieval import retrieve_from_paper, search_workspace_sources
from app.agents.agentic_rag.tools.external import fetch_external_background
from app.agents.agentic_rag.tools.paper_info import get_paper_metadata, get_concept_map, get_guided_questions
from app.agents.agentic_rag.tools.session import get_session_context
from app.agents.agentic_rag.tools.academic import search_academic_papers, get_citation_context, web_search

PAPER_QA_TOOLS = [
    retrieve_from_paper,
    fetch_external_background,
    get_paper_metadata,
    get_concept_map,
    get_guided_questions,
    get_session_context,
    search_academic_papers,
    get_citation_context,
    web_search,
]


def get_paper_qa_llm():
    return ChatAnthropic(
        model=get_model_name(),
        api_key=get_anthropic_api_key(),
        max_tokens=1024,
        temperature=0.2,
    ).bind_tools(PAPER_QA_TOOLS)


async def paper_qa_agent_node(state: dict) -> dict:
    """Agent node: LLM decides which tools to call (or stops)."""
    llm = get_paper_qa_llm()

    system_prompt = PAPER_QA_AGENT_SYSTEM.format(
        max_tool_calls=MAX_TOOL_CALLS_PAPER_QA,
        paper_title=state.get("paper_title", ""),
        paper_abstract=(state.get("paper_abstract", "") or "")[:400],
        session_context=state.get("session_summary", "First turn."),
    )

    messages = [SystemMessage(content=system_prompt)] + state["messages"]
    response = await llm.ainvoke(messages)

    # Track tool call count
    new_count = state.get("tool_call_count", 0)
    if response.tool_calls:
        new_count += len(response.tool_calls)

    return {"messages": [response], "tool_call_count": new_count}


# Prebuilt tool executor node
paper_qa_tool_node = ToolNode(PAPER_QA_TOOLS)
