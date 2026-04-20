"""
Paper QA subgraph — agentic tool-calling loop with chunk filtering,
structured generation, and self-correction grading.
"""
from __future__ import annotations

from langgraph.graph import StateGraph, END

from app.agents.agentic_rag.state import PaperQAState
from app.agents.agentic_rag.config import MAX_TOOL_CALLS_PAPER_QA, MAX_RETRY_COUNT
from app.agents.agentic_rag.nodes.agent import paper_qa_agent_node, paper_qa_tool_node
from app.agents.agentic_rag.nodes.chunk_filter import chunk_filter_node
from app.agents.agentic_rag.nodes.generate import generate_node
from app.agents.agentic_rag.nodes.grade import grade_node


def _should_continue_agent(state: dict) -> str:
    """After agent_node: route to tool_node if tool calls, else to chunk_filter."""
    messages = state.get("messages", [])
    if not messages:
        return "chunk_filter"

    last = messages[-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        if state.get("tool_call_count", 0) >= MAX_TOOL_CALLS_PAPER_QA:
            return "chunk_filter"
        return "tools"

    return "chunk_filter"


def _should_retry(state: dict) -> str:
    """After grade_node: retry if fail and under budget, else end."""
    if state.get("grade_result") == "pass":
        return "end"
    if state.get("retry_count", 0) >= MAX_RETRY_COUNT:
        return "end"
    return "retry"


def _inject_rewritten_query(state: dict) -> dict:
    """On retry: inject the rewritten query as a new human message for the agent."""
    from langchain_core.messages import HumanMessage

    rewritten = state.get("rewritten_query", "")
    if not rewritten:
        return {}

    return {
        "messages": [HumanMessage(content=f"[Retry with improved query] {rewritten}")],
        "all_retrieved_chunks": [],
        "filtered_chunks": [],
        "answer": None,
        "grade_result": "",
    }


def _collect_chunks_from_tools(state: dict) -> dict:
    """After tool execution, extract retrieved chunks from tool messages and accumulate."""
    import json
    messages = state.get("messages", [])
    new_chunks = []

    for msg in messages:
        if hasattr(msg, "type") and msg.type == "tool":
            try:
                content = msg.content
                if isinstance(content, str):
                    content = json.loads(content)
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and "chunk_id" in item:
                            new_chunks.append(item)
            except (json.JSONDecodeError, TypeError):
                pass

    return {"all_retrieved_chunks": new_chunks} if new_chunks else {}


def build_paper_qa_graph() -> StateGraph:
    """Build and compile the Paper QA subgraph."""
    graph = StateGraph(PaperQAState)

    graph.add_node("agent", paper_qa_agent_node)
    graph.add_node("tools", paper_qa_tool_node)
    graph.add_node("collect_chunks", _collect_chunks_from_tools)
    graph.add_node("chunk_filter", chunk_filter_node)
    graph.add_node("generate", generate_node)
    graph.add_node("grade", grade_node)
    graph.add_node("rewrite", _inject_rewritten_query)

    graph.set_entry_point("agent")

    graph.add_conditional_edges(
        "agent",
        _should_continue_agent,
        {"tools": "tools", "chunk_filter": "chunk_filter"},
    )

    graph.add_edge("tools", "collect_chunks")
    graph.add_edge("collect_chunks", "agent")

    graph.add_edge("chunk_filter", "generate")
    graph.add_edge("generate", "grade")

    graph.add_conditional_edges(
        "grade",
        _should_retry,
        {"end": END, "retry": "rewrite"},
    )

    graph.add_edge("rewrite", "agent")

    return graph.compile()


paper_qa_graph = build_paper_qa_graph()
