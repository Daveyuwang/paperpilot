"""
Console subgraph — agentic tool-calling loop for workspace operations.
No self-correction loop (unlike Paper QA). Budget cap at 8 tool calls.
"""
from __future__ import annotations

from langgraph.graph import StateGraph, END

from app.agents.agentic_rag.state import ConsoleState
from app.agents.agentic_rag.config import MAX_TOOL_CALLS_CONSOLE
from app.agents.agentic_rag.nodes.console_agent import console_agent_node, console_tool_node


def _should_continue(state: dict) -> str:
    """After console_agent: route to tools if tool calls, else end."""
    messages = state.get("messages", [])
    if not messages:
        return "end"

    last = messages[-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        if state.get("tool_call_count", 0) >= MAX_TOOL_CALLS_CONSOLE:
            return "end"
        return "tools"

    return "end"


def build_console_graph() -> StateGraph:
    """Build and compile the Console subgraph."""
    graph = StateGraph(ConsoleState)

    graph.add_node("agent", console_agent_node)
    graph.add_node("tools", console_tool_node)

    graph.set_entry_point("agent")

    graph.add_conditional_edges(
        "agent",
        _should_continue,
        {"tools": "tools", "end": END},
    )

    graph.add_edge("tools", "agent")

    return graph.compile()


console_graph = build_console_graph()
