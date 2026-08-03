"""Repair execution and partial/full replan state-transition tests."""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

from app.deep_research.models import (
    BudgetSnapshot,
    EvidenceIssue,
    EvidenceRepairDirective,
    Plan,
    PreSynthesisEvaluation,
    PreSynthesisEvaluationRun,
    PreSynthesisScores,
    RepairStage,
    RoutingDecision,
    SubQuestion,
    SubReport,
)
from app.deep_research.nodes import execute as execute_module
from app.deep_research.nodes import evaluate as evaluate_module
from app.deep_research.nodes import replan as replan_module


def _sub_question(question_id: str, query: str | None = None) -> SubQuestion:
    return SubQuestion(
        id=question_id,
        question=f"Question for {question_id}",
        search_queries=[query or f"original query {question_id}"],
        priority=1,
        rationale="Required by the objective.",
    )


def _sub_report(
    question_id: str,
    findings: str | None = None,
    *,
    confidence: float = 0.9,
) -> SubReport:
    return SubReport(
        sub_question_id=question_id,
        question=f"Question for {question_id}",
        findings=findings or f"Retained finding {question_id}",
        key_facts=[f"Retained fact {question_id}"],
        confidence=confidence,
        gaps="",
        sources=[],
    )


def _scores() -> PreSynthesisScores:
    return PreSynthesisScores(
        intent_alignment=90,
        must_answer_coverage=80,
        source_relevance=90,
        source_quality=90,
        source_diversity=90,
        source_recency=90,
        grounding_consistency=80,
        contradiction_handling=90,
        synthesis_readiness=80,
    )


def _repair_evaluation(target_ids: list[str]) -> PreSynthesisEvaluationRun:
    issue = EvidenceIssue(
        id="issue-local-gap",
        category="coverage_gap",
        severity="major",
        description="A local branch lacks decisive evidence.",
        affected_sub_question_ids=target_ids,
        source_urls=[],
    )
    directive = EvidenceRepairDirective(
        id="repair-local-gap",
        issue_ids=[issue.id],
        target_sub_question_ids=target_ids,
        objective="Acquire decisive evidence for the local gap.",
        suggested_queries=["updated primary query", "updated corroborating query"],
        acceptance_criteria=["Two independent retained sources close the gap."],
    )
    evaluation = PreSynthesisEvaluation(
        schema_version="pre-synthesis-evaluation.v1",
        rubric_version="pre-synthesis-rubric.v1",
        assessed_sub_question_ids=["sq-a", "sq-b"],
        scores=_scores(),
        issues=[issue],
        repair_directives=[directive],
        unresolved_questions=[],
        evaluation_limitations=[],
        summary="One local branch requires repair.",
    )
    return PreSynthesisEvaluationRun(
        status="completed",
        evaluation=evaluation,
        error_code=None,
        evaluator_model="test-evaluator",
        attempts=1,
        duration_ms=12,
    )


async def _call(node, state: dict[str, Any]) -> dict[str, Any]:
    update = node(state)
    if inspect.isawaitable(update):
        update = await update
    assert isinstance(update, dict)
    return update


def _route(update: dict[str, Any]) -> str | None:
    decision = update.get("controller_decision")
    if isinstance(decision, dict):
        return decision.get("route")
    if decision is not None:
        return getattr(decision, "route", None)
    return update.get("route")


def _assert_stops_incomplete(update: dict[str, Any]) -> None:
    assert update.get("terminal_reason")
    assert update.get("workflow_error_code")


def _routing_decision(route: str, target_ids: list[str]) -> RoutingDecision:
    stage = {
        "targeted_repair": RepairStage.TARGETED_REPAIR,
        "partial_replan": RepairStage.PARTIAL_REPLAN,
        "full_replan": RepairStage.FULL_REPLAN,
    }[route]
    return RoutingDecision(
        route=route,
        repair_stage=stage,
        reason_code=f"test_{route}",
        reason=f"Test decision for {route}.",
        affected_sub_question_ids=target_ids,
        issue_ids=["issue-local-gap"],
        major_issue_ids=["issue-local-gap"],
        weighted_overall_score=80,
        affected_priority_ratio=0.25,
        score_gain=None,
        closed_major_issue_ids=[],
        fingerprint="f" * 64,
        escalated_from=None,
        budget=BudgetSnapshot(),
    )


@pytest.mark.parametrize(
    ("execute_status", "expected_route"),
    [
        ("completed", "evaluate"),
        ("failed", "stop_incomplete"),
        ("unknown", "stop_incomplete"),
        (None, "stop_incomplete"),
    ],
)
def test_route_after_execute_is_fail_closed(
    execute_status: str | None,
    expected_route: str,
) -> None:
    state = {} if execute_status is None else {"execute_status": execute_status}

    assert execute_module.route_after_execute(state) == expected_route


@pytest.mark.asyncio
async def test_targeted_repair_updates_only_selected_queries() -> None:
    questions = [_sub_question("sq-a"), _sub_question("sq-b")]
    state = {
        "sub_questions": questions,
        "sub_reports": [_sub_report("sq-a"), _sub_report("sq-b")],
        "pre_synthesis_evaluation_run": _repair_evaluation(["sq-a"]),
        "controller_decision": _routing_decision("targeted_repair", ["sq-a"]),
    }

    update = await _call(replan_module.prepare_targeted_repair_node, state)
    updated = {question.id: question for question in update["sub_questions"]}

    assert update["execution_target_ids"] == ["sq-a"]
    assert updated["sq-a"].search_queries == [
        "updated primary query",
        "updated corroborating query",
        "original query sq-a",
    ]
    assert updated["sq-b"].search_queries == questions[1].search_queries


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_ids",
    [[], ["unknown"], ["sq-a", "sq-a"]],
)
async def test_invalid_target_batches_never_execute_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    target_ids: list[str],
) -> None:
    calls: list[str] = []

    async def forbidden_execute(*args, **kwargs):
        del args, kwargs
        calls.append("called")
        raise AssertionError("invalid target IDs must never execute")

    monkeypatch.setattr(execute_module, "_execute_single", forbidden_execute)
    state = {
        "sub_questions": [_sub_question("sq-a"), _sub_question("sq-b")],
        "sub_reports": [_sub_report("sq-a"), _sub_report("sq-b")],
        "failed_queries": [],
        "execution_target_ids": target_ids,
    }

    update = await execute_module.execute_node(state)

    assert calls == []
    _assert_stops_incomplete(update)
    assert update.get("execution_target_ids") is None
    assert execute_module.route_after_execute(update) == "stop_incomplete"


@pytest.mark.asyncio
async def test_targeted_execution_retries_only_selected_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_a = _sub_report("sq-a", "old a")
    old_b = _sub_report("sq-b", "keep b")
    repaired_a = _sub_report("sq-a", "repaired a")
    calls: list[str] = []

    async def fake_execute(sub_question, *_args):
        calls.append(sub_question.id)
        return repaired_a, None

    monkeypatch.setattr(execute_module, "_execute_single", fake_execute)
    update = await execute_module.execute_node(
        {
            "sub_questions": [_sub_question("sq-a"), _sub_question("sq-b")],
            "sub_reports": [old_a, old_b],
            "failed_queries": [],
            "execution_target_ids": ["sq-a"],
        }
    )
    by_id = {report.sub_question_id: report for report in update["sub_reports"]}

    assert calls == ["sq-a"]
    assert by_id["sq-a"].findings == "repaired a"
    assert by_id["sq-b"] is old_b


@pytest.mark.asyncio
async def test_failed_targeted_retry_preserves_prior_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained = _sub_report("sq-a", "last known good evidence")
    fallback = _sub_report("sq-a", "failed retry placeholder", confidence=0.0)

    async def failed_retry(sub_question, *_args):
        return fallback, {
            "sub_question_id": sub_question.id,
            "query": sub_question.search_queries,
            "error_code": "provider_error",
            "reason": "The provider could not complete this step.",
        }

    monkeypatch.setattr(execute_module, "_execute_single", failed_retry)
    update = await execute_module.execute_node(
        {
            "sub_questions": [_sub_question("sq-a")],
            "sub_reports": [retained],
            "failed_queries": [],
            "execution_target_ids": ["sq-a"],
        }
    )

    assert update["sub_reports"] == [retained]
    assert update["failed_queries"][0]["error_code"] == "provider_error"


@pytest.mark.asyncio
async def test_gather_exception_preserves_prior_report_and_sanitizes_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained = _sub_report("sq-a", "last known good evidence")
    secret = "raw-gather-secret"

    async def escaped_failure(*_args):
        raise RuntimeError(secret)

    monkeypatch.setattr(execute_module, "_execute_single", escaped_failure)
    update = await execute_module.execute_node(
        {
            "sub_questions": [_sub_question("sq-a")],
            "sub_reports": [retained],
            "failed_queries": [],
            "execution_target_ids": ["sq-a"],
        }
    )

    assert update["sub_reports"] == [retained]
    assert update["failed_queries"][0]["error_code"] == "execution_error"
    assert secret not in json.dumps(update, default=str)


@pytest.mark.asyncio
async def test_execution_failure_state_is_categorical_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "raw-provider-secret-should-not-survive"

    async def fail_search(_queries):
        raise RuntimeError(f"provider exploded: {secret}")

    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(execute_module, "tavily_search", fail_search)
    monkeypatch.setattr(execute_module, "adispatch_custom_event", ignore_event)

    report, failure = await execute_module._execute_single(
        _sub_question("sq-a"),
        0,
        1,
        {"topic": "test"},
    )
    serialized = json.dumps(
        {
            "report": report.model_dump() if report else None,
            "failure": failure,
        }
    )

    assert secret not in serialized
    assert failure is not None
    assert failure["reason"] in {
        "search_error",
        "provider_error",
        "execution_error",
        "transient_error",
    }


def test_evaluator_and_repair_prompts_exclude_raw_failure_reasons() -> None:
    secret = "raw-provider-stack-and-token"
    questions = [_sub_question("sq-a"), _sub_question("sq-b")]
    state = {
        "topic": "Prompt sanitization test",
        "sub_questions": questions,
        "sub_reports": [_sub_report("sq-a"), _sub_report("sq-b")],
        "failed_queries": [
            {
                "sub_question_id": "sq-a",
                "query": ["safe query"],
                "error_code": "provider_error",
                "reason": secret,
            }
        ],
        "controller_decision": _routing_decision("partial_replan", ["sq-a"]),
        "plan_version": 1,
    }

    evaluator_payload = evaluate_module._build_research_corpus_json(state)
    repair_payload = replan_module._repair_context_json(
        state,
        replan_module._decision(state),
    )

    assert secret not in evaluator_payload
    assert secret not in repair_payload
    assert "provider_error" in evaluator_payload
    assert "provider_error" in repair_payload


class _FakeStructuredLLM:
    def __init__(self, response: Plan | None = None, error: Exception | None = None):
        self.response = response
        self.error = error

    async def ainvoke(self, _messages):
        if self.error is not None:
            raise self.error
        return self.response


def _patch_replan_llm(
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeStructuredLLM,
) -> None:
    monkeypatch.setattr(
        replan_module,
        "make_structured_llm",
        lambda *_args, **_kwargs: fake,
    )


def _replan_state(route: str, target_ids: list[str]) -> dict[str, Any]:
    return {
        "topic": "Replan state test",
        "sub_questions": [
            _sub_question("sq-a"),
            _sub_question("sq-b"),
            _sub_question("sq-c"),
        ],
        "sub_reports": [
            _sub_report("sq-a"),
            _sub_report("sq-b"),
            _sub_report("sq-c"),
        ],
        "failed_queries": [
            {
                "sub_question_id": "sq-a",
                "query": ["old a"],
                "reason": "provider_error",
            },
            {
                "sub_question_id": "sq-c",
                "query": ["keep c"],
                "reason": "no_results",
            },
        ],
        "controller_decision": _routing_decision(route, target_ids),
        "plan_version": 1,
    }


@pytest.mark.asyncio
async def test_partial_replan_preserves_unaffected_and_prunes_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacement = Plan(
        sub_questions=[
            _sub_question("sq-new-a", "new query a"),
            _sub_question("sq-new-b", "new query b"),
        ],
        overall_approach="Replace the two affected branches.",
    )
    _patch_replan_llm(monkeypatch, _FakeStructuredLLM(response=replacement))
    state = _replan_state("partial_replan", ["sq-a", "sq-b"])

    update = await _call(replan_module.partial_replan_node, state)

    active_ids = {question.id for question in update["sub_questions"]}
    report_ids = {report.sub_question_id for report in update["sub_reports"]}
    failure_ids = {failure["sub_question_id"] for failure in update["failed_queries"]}
    assert active_ids == {"sq-c", "sq-new-a", "sq-new-b"}
    assert report_ids == {"sq-c"}
    assert failure_ids == {"sq-c"}
    assert report_ids <= active_ids
    assert failure_ids <= active_ids
    assert update["execution_target_ids"] == ["sq-new-a", "sq-new-b"]
    assert update["plan_version"] == 2


@pytest.mark.asyncio
async def test_full_replan_clears_old_active_artifacts_and_increments_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacement = Plan(
        sub_questions=[
            _sub_question("sq-new-a", "fresh query a"),
            _sub_question("sq-new-b", "fresh query b"),
        ],
        overall_approach="Replace the complete plan.",
    )
    _patch_replan_llm(monkeypatch, _FakeStructuredLLM(response=replacement))
    state = _replan_state("full_replan", ["sq-a", "sq-b", "sq-c"])
    state["final_report"] = {"stale": True}

    update = await _call(replan_module.full_replan_node, state)

    assert [question.id for question in update["sub_questions"]] == [
        "sq-new-a",
        "sq-new-b",
    ]
    assert update["sub_reports"] == []
    assert update["failed_queries"] == []
    assert update.get("final_report") is None
    assert update["plan_version"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("target_ids", [[], ["sq-unknown"], ["sq-a", "sq-a"]])
async def test_full_replan_rejects_invalid_affected_ids_before_calling_llm(
    monkeypatch: pytest.MonkeyPatch,
    target_ids: list[str],
) -> None:
    def forbidden_factory(*_args, **_kwargs):
        raise AssertionError("invalid full-replan targets must not call the LLM")

    monkeypatch.setattr(replan_module, "make_structured_llm", forbidden_factory)
    update = await _call(
        replan_module.full_replan_node,
        _replan_state("full_replan", target_ids),
    )

    _assert_stops_incomplete(update)
    assert update["workflow_error_code"] == "invalid_repair_targets"


@pytest.mark.asyncio
@pytest.mark.parametrize("node_name", ["partial_replan_node", "full_replan_node"])
@pytest.mark.parametrize("replacement_count", [0, 9])
async def test_repair_plan_enforces_one_to_eight_replacements(
    monkeypatch: pytest.MonkeyPatch,
    node_name: str,
    replacement_count: int,
) -> None:
    replacement = Plan(
        sub_questions=[
            _sub_question(f"sq-new-{index}") for index in range(replacement_count)
        ],
        overall_approach="Invalid replacement count fixture.",
    )
    _patch_replan_llm(monkeypatch, _FakeStructuredLLM(response=replacement))
    route = "partial_replan" if node_name.startswith("partial") else "full_replan"
    targets = ["sq-a"] if route == "partial_replan" else ["sq-a", "sq-b", "sq-c"]

    update = await _call(getattr(replan_module, node_name), _replan_state(route, targets))

    _assert_stops_incomplete(update)
    assert update["workflow_error_code"] == f"{route}_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("node_name", ["partial_replan_node", "full_replan_node"])
async def test_repair_planner_failure_stops_incomplete_without_raw_error(
    monkeypatch: pytest.MonkeyPatch,
    node_name: str,
) -> None:
    secret = "raw-planner-secret"
    _patch_replan_llm(
        monkeypatch,
        _FakeStructuredLLM(error=RuntimeError(f"planner unavailable {secret}")),
    )
    route = "partial_replan" if node_name.startswith("partial") else "full_replan"
    state = _replan_state(route, ["sq-a"])

    update = await _call(getattr(replan_module, node_name), state)
    serialized = json.dumps(update, default=str)

    _assert_stops_incomplete(update)
    assert secret not in serialized
