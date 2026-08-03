from __future__ import annotations

from langgraph.graph import StateGraph, END

from app.deep_research.context import DeepResearchContext
from app.deep_research.state import DeepResearchState
from app.deep_research.nodes.plan import plan_node
from app.deep_research.nodes.execute import execute_node, route_after_execute
from app.deep_research.nodes.evaluate import evidence_evaluate_node
from app.deep_research.nodes.controller import (
    controller_node,
    finalize_complete_node,
    finalize_incomplete_node,
    route_after_controller,
)
from app.deep_research.nodes.evaluate_report import post_synthesis_evaluate_node
from app.deep_research.nodes.post_controller import (
    post_synthesis_controller_node,
    route_after_post_controller,
)
from app.deep_research.nodes.replan import (
    full_replan_node,
    partial_replan_node,
    prepare_targeted_repair_node,
    route_after_repair_preparation,
)
from app.deep_research.nodes.revise_report import (
    revise_report_node,
    route_after_report_revision,
)
from app.deep_research.nodes.synthesize import synthesize_node
from app.deep_research.nodes.persist_artifacts import (
    persist_initial_plan_node,
    persist_post_controller_node,
    persist_post_evaluation_node,
    persist_pre_controller_node,
    persist_pre_evaluation_node,
    persist_repair_plan_node,
    persist_revised_candidate_node,
    persist_sub_reports_node,
    persist_synthesis_candidate_node,
    persist_terminal_node,
)


DEEP_RESEARCH_RECURSION_LIMIT = 128


def build_graph() -> StateGraph:
    graph = StateGraph(
        DeepResearchState,
        context_schema=DeepResearchContext,
    )

    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("evaluate", evidence_evaluate_node)
    graph.add_node("controller", controller_node)
    graph.add_node("targeted_repair", prepare_targeted_repair_node)
    graph.add_node("partial_replan", partial_replan_node)
    graph.add_node("full_replan", full_replan_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("evaluate_report", post_synthesis_evaluate_node)
    graph.add_node("post_controller", post_synthesis_controller_node)
    graph.add_node("revise_report", revise_report_node)
    graph.add_node("finalize_complete", finalize_complete_node)
    graph.add_node("finalize_incomplete", finalize_incomplete_node)
    graph.add_node("persist_initial_plan", persist_initial_plan_node)
    graph.add_node("persist_sub_reports", persist_sub_reports_node)
    graph.add_node("persist_pre_evaluation", persist_pre_evaluation_node)
    graph.add_node("persist_pre_controller", persist_pre_controller_node)
    graph.add_node("persist_repair_plan", persist_repair_plan_node)
    graph.add_node(
        "persist_synthesis_candidate", persist_synthesis_candidate_node
    )
    graph.add_node("persist_post_evaluation", persist_post_evaluation_node)
    graph.add_node("persist_post_controller", persist_post_controller_node)
    graph.add_node("persist_revised_candidate", persist_revised_candidate_node)
    graph.add_node("persist_terminal", persist_terminal_node)

    graph.set_entry_point("plan")
    graph.add_edge("plan", "persist_initial_plan")
    graph.add_edge("persist_initial_plan", "execute")
    graph.add_edge("execute", "persist_sub_reports")
    graph.add_conditional_edges(
        "persist_sub_reports",
        route_after_execute,
        {
            "evaluate": "evaluate",
            "stop_incomplete": "finalize_incomplete",
        },
    )
    graph.add_edge("evaluate", "persist_pre_evaluation")
    graph.add_edge("persist_pre_evaluation", "controller")
    graph.add_edge("controller", "persist_pre_controller")
    graph.add_conditional_edges(
        "persist_pre_controller",
        route_after_controller,
        {
            "accept": "synthesize",
            "targeted_repair": "targeted_repair",
            "partial_replan": "partial_replan",
            "full_replan": "full_replan",
            "stop_incomplete": "finalize_incomplete",
        },
    )
    for repair_node in ("targeted_repair", "partial_replan", "full_replan"):
        graph.add_edge(repair_node, "persist_repair_plan")
    graph.add_conditional_edges(
        "persist_repair_plan",
        route_after_repair_preparation,
        {
            "execute": "execute",
            "stop_incomplete": "finalize_incomplete",
        },
    )
    graph.add_edge("synthesize", "persist_synthesis_candidate")
    graph.add_edge("persist_synthesis_candidate", "evaluate_report")
    graph.add_edge("evaluate_report", "persist_post_evaluation")
    graph.add_edge("persist_post_evaluation", "post_controller")
    graph.add_edge("post_controller", "persist_post_controller")
    graph.add_conditional_edges(
        "persist_post_controller",
        route_after_post_controller,
        {
            "accept": "finalize_complete",
            "targeted_synthesis": "revise_report",
            "targeted_evidence": "targeted_repair",
            "partial_replan": "partial_replan",
            "full_replan": "full_replan",
            "stop_incomplete": "finalize_incomplete",
        },
    )
    graph.add_edge("revise_report", "persist_revised_candidate")
    graph.add_conditional_edges(
        "persist_revised_candidate",
        route_after_report_revision,
        {
            "evaluate_report": "evaluate_report",
            "stop_incomplete": "finalize_incomplete",
        },
    )
    graph.add_edge("finalize_complete", "persist_terminal")
    graph.add_edge("finalize_incomplete", "persist_terminal")
    graph.add_edge("persist_terminal", END)

    return graph


compiled_graph = build_graph().compile()
