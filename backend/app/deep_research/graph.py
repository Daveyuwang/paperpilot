from __future__ import annotations

from langgraph.graph import StateGraph, END

from app.deep_research.state import DeepResearchState
from app.deep_research.nodes.plan import plan_node
from app.deep_research.nodes.execute import execute_node
from app.deep_research.nodes.replan import replan_node, should_continue
from app.deep_research.nodes.synthesize import synthesize_node


def build_graph() -> StateGraph:
    graph = StateGraph(DeepResearchState)

    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("evaluate", lambda state: state)
    graph.add_node("replan", replan_node)
    graph.add_node("synthesize", synthesize_node)

    graph.set_entry_point("plan")
    graph.add_edge("plan", "execute")
    graph.add_edge("execute", "evaluate")
    graph.add_conditional_edges(
        "evaluate",
        should_continue,
        {"replan": "replan", "synthesize": "synthesize"},
    )
    graph.add_edge("replan", "execute")
    graph.add_edge("synthesize", END)

    return graph


compiled_graph = build_graph().compile()
