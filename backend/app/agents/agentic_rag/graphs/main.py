"""
Top-level graph — routes user messages to Paper QA, Console, or direct response.
"""
from __future__ import annotations

from typing import Annotated, Literal

from langgraph.graph import StateGraph, END, MessagesState
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.agentic_rag.nodes.router import router_node


class MainState(MessagesState):
    """Top-level state with routing info."""
    route: str
    # Paper QA context
    paper_id: str
    session_id: str
    guest_id: str
    paper_title: str
    paper_abstract: str
    session_summary: str
    # Console context
    workspace_id: str
    active_paper_id: str
    workspace_title: str
    paper_count: int
    active_paper_name: str
    workspace_snapshot: str


def _route_dispatch(state: dict) -> str:
    """Dispatch based on router classification."""
    route = state.get("route", "")
    if route == "paper_qa":
        return "paper_qa"
    elif route == "console":
        return "console"
    else:
        return "direct_response"


async def direct_response_node(state: dict) -> dict:
    """Handle simple messages that need no tools (greetings, thanks, meta-questions)."""
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import SystemMessage
    from app.agents.agentic_rag.config import get_model_name, get_anthropic_api_key

    llm = ChatAnthropic(
        model=get_model_name(),
        api_key=get_anthropic_api_key(),
        max_tokens=500,
        temperature=0.3,
    )

    system = (
        "You are PaperPilot, a friendly research assistant. "
        "Respond briefly and helpfully to greetings, thanks, or simple questions about the tool. "
        "Keep responses under 2 sentences."
    )

    response = await llm.ainvoke(
        [SystemMessage(content=system)] + state["messages"]
    )
    return {"messages": [response]}


async def paper_qa_subgraph_node(state: dict) -> dict:
    """Invoke the Paper QA subgraph."""
    from app.agents.agentic_rag.graphs.paper_qa import paper_qa_graph

    input_state = {
        "messages": state["messages"],
        "paper_id": state.get("paper_id", ""),
        "session_id": state.get("session_id", ""),
        "guest_id": state.get("guest_id", ""),
        "paper_title": state.get("paper_title", ""),
        "paper_abstract": state.get("paper_abstract", ""),
        "session_summary": state.get("session_summary", ""),
        "all_retrieved_chunks": [],
        "filtered_chunks": [],
        "answer": None,
        "grade_result": "",
        "rewritten_query": "",
        "retry_count": 0,
        "tool_call_count": 0,
        "guide_questions": [],
        "covered_question_ids": [],
        "explained_terms": [],
        "recent_messages": [],
        "session_language": "",
    }

    result = await paper_qa_graph.ainvoke(input_state)

    # Extract the final answer and format as AI message
    answer = result.get("answer")
    if answer:
        answer_text = answer.get("direct_answer", "")
        return {"messages": [AIMessage(content=answer_text, additional_kwargs={"answer_json": answer})]}

    # Fallback: return last AI message from subgraph
    for msg in reversed(result.get("messages", [])):
        if hasattr(msg, "type") and msg.type == "ai" and msg.content:
            return {"messages": [msg]}

    return {"messages": [AIMessage(content="I couldn't find a relevant answer in the paper.")]}


async def console_subgraph_node(state: dict) -> dict:
    """Invoke the Console subgraph."""
    from app.agents.agentic_rag.graphs.console import console_graph

    input_state = {
        "messages": state["messages"],
        "workspace_id": state.get("workspace_id", ""),
        "session_id": state.get("session_id", ""),
        "active_paper_id": state.get("active_paper_id", ""),
        "tool_call_count": 0,
    }

    result = await console_graph.ainvoke(input_state)

    # Return last AI message from console subgraph
    for msg in reversed(result.get("messages", [])):
        if hasattr(msg, "type") and msg.type == "ai" and msg.content:
            return {"messages": [msg]}

    return {"messages": [AIMessage(content="I'm not sure how to help with that. Could you rephrase?")]}


def build_main_graph() -> StateGraph:
    """Build and compile the top-level routing graph."""
    graph = StateGraph(MainState)

    graph.add_node("router", router_node)
    graph.add_node("paper_qa", paper_qa_subgraph_node)
    graph.add_node("console", console_subgraph_node)
    graph.add_node("direct_response", direct_response_node)

    graph.set_entry_point("router")

    graph.add_conditional_edges(
        "router",
        _route_dispatch,
        {
            "paper_qa": "paper_qa",
            "console": "console",
            "direct_response": "direct_response",
        },
    )

    graph.add_edge("paper_qa", END)
    graph.add_edge("console", END)
    graph.add_edge("direct_response", END)

    return graph.compile()


main_graph = build_main_graph()
