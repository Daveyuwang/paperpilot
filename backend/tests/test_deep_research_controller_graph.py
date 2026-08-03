"""Graph-level checks for pre-synthesis accept and bounded recovery loops."""

from __future__ import annotations

from typing import Any

import pytest

from app.deep_research import graph as graph_module
from app.deep_research.models import (
    BudgetSnapshot,
    EvidenceIssue,
    EvidenceRepairDirective,
    PreSynthesisEvaluation,
    PreSynthesisEvaluationRun,
    PreSynthesisScores,
    ReportSection,
    ResearchReport,
    SourceRef,
    SubQuestion,
    SubReport,
)


def _question(question_id: str) -> SubQuestion:
    return SubQuestion(
        id=question_id,
        question=f"Question {question_id}",
        search_queries=[f"query {question_id}"],
        priority=1,
        rationale="Required for the graph test.",
    )


def _report(question_id: str) -> SubReport:
    return SubReport(
        sub_question_id=question_id,
        question=f"Question {question_id}",
        findings=f"Finding {question_id}",
        key_facts=[f"Fact {question_id}"],
        confidence=0.9,
        gaps="",
        sources=[
            SourceRef(
                url=f"https://evidence.example/{question_id}",
                title=f"Evidence {question_id}",
            )
        ],
    )


def _scores() -> PreSynthesisScores:
    return PreSynthesisScores(
        intent_alignment=90,
        must_answer_coverage=90,
        source_relevance=90,
        source_quality=90,
        source_diversity=90,
        source_recency=90,
        grounding_consistency=90,
        contradiction_handling=90,
        synthesis_readiness=90,
    )


def _evaluation(*, repair: bool) -> PreSynthesisEvaluationRun:
    ids = ["sq-a", "sq-b", "sq-c", "sq-d"]
    issues: list[EvidenceIssue] = []
    directives: list[EvidenceRepairDirective] = []
    if repair:
        issues = [
            EvidenceIssue(
                id="issue-repeat",
                category="coverage_gap",
                severity="major",
                description="The same local evidence gap remains unresolved.",
                affected_sub_question_ids=["sq-a"],
                source_urls=[],
            )
        ]
        directives = [
            EvidenceRepairDirective(
                id="repair-repeat",
                issue_ids=["issue-repeat"],
                target_sub_question_ids=["sq-a"],
                objective="Close the local gap.",
                suggested_queries=["repeat query"],
                acceptance_criteria=["Retain decisive primary evidence."],
            )
        ]
    return PreSynthesisEvaluationRun(
        status="completed",
        evaluation=PreSynthesisEvaluation(
            schema_version="pre-synthesis-evaluation.v1",
            rubric_version="pre-synthesis-rubric.v1",
            assessed_sub_question_ids=ids,
            scores=_scores(),
            issues=issues,
            repair_directives=directives,
            unresolved_questions=[],
            evaluation_limitations=[],
            summary="Graph evaluator fixture.",
        ),
        error_code=None,
        evaluator_model="test-evaluator",
        attempts=1,
        duration_ms=5,
    )


def _final_report() -> ResearchReport:
    return ResearchReport(
        title="Accepted report",
        executive_summary="Supported summary.",
        sections=[ReportSection(heading="Evidence", content="Supported content.")],
        key_findings=["Supported finding."],
        limitations="Fixture limitations.",
        sources=[SourceRef(url="https://evidence.example/sq-a", title="Evidence")],
    )


def _initial_state() -> dict[str, Any]:
    return {
        "topic": "Graph test topic",
        "user_sources": [],
        "depth": "standard",
        "sub_questions": [],
        "sub_reports": [],
        "failed_queries": [],
        "plan_version": 1,
        "budget_snapshot": BudgetSnapshot(),
        "routing_history": [],
        "recovery_fingerprints": [],
    }


@pytest.mark.asyncio
async def test_graph_accept_path_evaluates_then_synthesizes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    questions = [_question(question_id) for question_id in ["sq-a", "sq-b", "sq-c", "sq-d"]]
    reports = [_report(question.id) for question in questions]

    async def plan(_state):
        trace.append("plan")
        return {"sub_questions": questions, "plan_version": 1}

    async def execute(_state):
        trace.append("execute")
        return {
            "sub_reports": reports,
            "failed_queries": [],
            "execute_status": "completed",
        }

    async def evaluate(_state):
        trace.append("evaluate")
        return {"pre_synthesis_evaluation_run": _evaluation(repair=False)}

    async def synthesize(_state):
        trace.append("synthesize")
        return {
            "candidate_report": _final_report(),
            "final_report": None,
            "report_accepted": False,
        }

    async def evaluate_report(state):
        trace.append("evaluate_report")
        assert state["candidate_report"].title == "Accepted report"
        assert state["final_report"] is None
        assert state["report_accepted"] is False
        return {"post_evaluation_history": []}

    def post_controller(_state):
        trace.append("post_controller")
        return {"report_accepted": False}

    def finalize_complete(state):
        trace.append("finalize_complete")
        assert state["final_report"] is None
        assert state["report_accepted"] is False
        return {
            "final_report": state["candidate_report"],
            "report_accepted": True,
            "terminal_status": "completed",
        }

    monkeypatch.setattr(graph_module, "plan_node", plan)
    monkeypatch.setattr(graph_module, "execute_node", execute)
    monkeypatch.setattr(graph_module, "evidence_evaluate_node", evaluate)
    monkeypatch.setattr(graph_module, "synthesize_node", synthesize)
    monkeypatch.setattr(graph_module, "post_synthesis_evaluate_node", evaluate_report)
    monkeypatch.setattr(graph_module, "post_synthesis_controller_node", post_controller)
    monkeypatch.setattr(
        graph_module,
        "route_after_post_controller",
        lambda _state: "accept",
    )
    monkeypatch.setattr(graph_module, "finalize_complete_node", finalize_complete)

    result = await graph_module.build_graph().compile().ainvoke(_initial_state())

    assert trace == [
        "plan",
        "execute",
        "evaluate",
        "synthesize",
        "evaluate_report",
        "post_controller",
        "finalize_complete",
    ]
    assert result["final_report"].title == "Accepted report"
    assert result["report_accepted"] is True
    decision = result["controller_decision"]
    route = decision["route"] if isinstance(decision, dict) else decision.route
    assert route == "accept"
    assert result.get("terminal_status") != "incomplete"


@pytest.mark.asyncio
async def test_graph_never_pass_evaluation_terminates_finitely_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"execute": 0, "evaluate": 0, "synthesize": 0}
    recovery_trace: list[str] = []
    questions = [_question(question_id) for question_id in ["sq-a", "sq-b", "sq-c", "sq-d"]]
    reports = [_report(question.id) for question in questions]

    async def plan(_state):
        return {"sub_questions": questions, "plan_version": 1}

    async def execute(_state):
        calls["execute"] += 1
        return {
            "sub_reports": reports,
            "failed_queries": [],
            "execution_target_ids": None,
            "execute_status": "completed",
        }

    async def evaluate(_state):
        calls["evaluate"] += 1
        return {"pre_synthesis_evaluation_run": _evaluation(repair=True)}

    async def prepare_targeted(state):
        recovery_trace.append("targeted_repair")
        return {
            "execution_target_ids": ["sq-a"],
            "repair_preparation_status": "ready",
        }

    async def partial_replan(state):
        recovery_trace.append("partial_replan")
        return {
            "execution_target_ids": ["sq-a"],
            "repair_preparation_status": "ready",
            "plan_version": state.get("plan_version", 1) + 1,
        }

    async def full_replan(state):
        recovery_trace.append("full_replan")
        return {
            "execution_target_ids": ["sq-a", "sq-b", "sq-c", "sq-d"],
            "repair_preparation_status": "ready",
            "plan_version": state.get("plan_version", 1) + 1,
            "sub_reports": [],
            "failed_queries": [],
        }

    async def forbidden_synthesize(_state):
        calls["synthesize"] += 1
        raise AssertionError("a never-passing evaluation must not synthesize")

    monkeypatch.setattr(graph_module, "plan_node", plan)
    monkeypatch.setattr(graph_module, "execute_node", execute)
    monkeypatch.setattr(graph_module, "evidence_evaluate_node", evaluate)
    monkeypatch.setattr(graph_module, "prepare_targeted_repair_node", prepare_targeted)
    monkeypatch.setattr(graph_module, "partial_replan_node", partial_replan)
    monkeypatch.setattr(graph_module, "full_replan_node", full_replan)
    monkeypatch.setattr(graph_module, "synthesize_node", forbidden_synthesize)

    result = await graph_module.build_graph().compile().ainvoke(
        _initial_state(),
        config={"recursion_limit": graph_module.DEEP_RESEARCH_RECURSION_LIMIT},
    )

    assert result["terminal_status"] == "incomplete"
    assert result["terminal_reason"]
    assert result["final_report"] is None
    assert calls["synthesize"] == 0
    assert recovery_trace == ["targeted_repair", "partial_replan", "full_replan"]
    assert calls["evaluate"] == 4
    assert calls["execute"] == 4
