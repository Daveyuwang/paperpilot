"""Regression tests for Deep Research report and retry state semantics."""

from __future__ import annotations

import pytest
from langgraph.graph import END, StateGraph

from app.deep_research.models import SubQuestion, SubReport
from app.deep_research.nodes import execute as execute_module
from app.deep_research.state import DeepResearchState


def _noop_evaluate_node(_state: DeepResearchState) -> dict:
    return {}


def _sub_question(question_id: str) -> SubQuestion:
    return SubQuestion(
        id=question_id,
        question=f"Question for {question_id}",
        search_queries=[f"query for {question_id}"],
        priority=1,
        rationale="Needed by the regression test.",
    )


def _sub_report(
    question_id: str,
    findings: str,
    *,
    confidence: float = 0.8,
) -> SubReport:
    return SubReport(
        sub_question_id=question_id,
        question=f"Question for {question_id}",
        findings=findings,
        key_facts=[findings],
        confidence=confidence,
        gaps="",
        sources=[],
    )


def test_noop_evaluation_does_not_duplicate_sub_reports() -> None:
    reports = [
        _sub_report("sq-a", "finding a"),
        _sub_report("sq-b", "finding b"),
    ]
    state = {"sub_reports": reports}

    assert _noop_evaluate_node(state) == {}

    for evaluator in (_noop_evaluate_node, lambda current: current):
        graph = StateGraph(DeepResearchState)
        graph.add_node("evaluate", evaluator)
        graph.set_entry_point("evaluate")
        graph.add_edge("evaluate", END)

        result = graph.compile().invoke(state)
        assert [report.sub_question_id for report in result["sub_reports"]] == [
            "sq-a",
            "sq-b",
        ]
        assert [report.findings for report in result["sub_reports"]] == [
            "finding a",
            "finding b",
        ]


@pytest.mark.asyncio
async def test_targeted_execute_replaces_report_and_preserves_unaffected_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    weak_report = _sub_report("sq-weak", "stale weak finding", confidence=0.1)
    healthy_report = _sub_report("sq-healthy", "keep this finding", confidence=0.95)
    retried_report = _sub_report("sq-weak", "repaired finding", confidence=0.9)
    calls: list[str] = []

    async def fake_execute_single(sub_question, sq_index, sq_total, state, runtime):
        del sq_index, sq_total, state
        assert runtime is None
        calls.append(sub_question.id)
        return retried_report, None

    monkeypatch.setattr(execute_module, "_execute_single", fake_execute_single)

    previous_failures = [
        {
            "sub_question_id": "sq-weak",
            "query": ["old weak query"],
            "reason": "old weak failure",
        },
        {
            "sub_question_id": "sq-unrelated",
            "query": ["unrelated query"],
            "reason": "unrelated failure",
        },
    ]
    state = {
        "sub_questions": [
            _sub_question("sq-weak"),
            _sub_question("sq-healthy"),
        ],
        "execution_target_ids": ["sq-weak"],
        "sub_reports": [weak_report, healthy_report],
        "failed_queries": previous_failures,
    }

    update = await execute_module.execute_node(state)
    updated_reports = update["sub_reports"]
    reports_by_id = {report.sub_question_id: report for report in updated_reports}

    assert calls == ["sq-weak"]
    assert len(updated_reports) == 2
    assert reports_by_id["sq-weak"].findings == "repaired finding"
    assert reports_by_id["sq-healthy"] is healthy_report

    # A successful retry clears its stale failure without erasing failures for
    # sub-questions that were not part of this execution batch.
    assert update["failed_queries"] == [previous_failures[1]]


def test_empty_report_update_explicitly_resets_reports() -> None:
    reports = [
        _sub_report("sq-a", "finding a"),
        _sub_report("sq-b", "finding b"),
    ]

    graph = StateGraph(DeepResearchState)
    graph.add_node("reset", lambda state: {"sub_reports": []})
    graph.set_entry_point("reset")
    graph.add_edge("reset", END)

    result = graph.compile().invoke({"sub_reports": reports})
    assert result["sub_reports"] == []
