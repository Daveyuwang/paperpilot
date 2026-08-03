"""Golden-case tests for the deterministic pre-synthesis controller.

The LLM evaluator diagnoses evidence quality.  These tests intentionally treat
its output as data: only the deterministic controller may select the next
workflow route.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from pydantic import ValidationError

from app.deep_research.models import (
    BudgetSnapshot,
    EvidenceIssue,
    EvidenceRepairDirective,
    PreSynthesisEvaluation,
    PreSynthesisEvaluationRun,
    PreSynthesisScores,
    SourceRef,
    SubQuestion,
    SubReport,
)
from app.deep_research.nodes import controller as controller_module


ROUTES = {
    "accept",
    "targeted_repair",
    "partial_replan",
    "full_replan",
    "stop_incomplete",
}


def _sub_question(question_id: str, *, priority: int = 1) -> SubQuestion:
    return SubQuestion(
        id=question_id,
        question=f"Research question {question_id}",
        search_queries=[f"primary evidence {question_id}"],
        priority=priority,
        rationale="Required by the research objective.",
    )


def _sub_report(question_id: str) -> SubReport:
    return SubReport(
        sub_question_id=question_id,
        question=f"Research question {question_id}",
        findings=f"Supported finding for {question_id}.",
        key_facts=[f"Supported fact for {question_id}."],
        confidence=0.9,
        gaps="",
        sources=[
            SourceRef(
                url=f"https://evidence.example/{question_id}",
                title=f"Primary evidence for {question_id}",
            )
        ],
    )


def _scores(default: int = 90, **overrides: int) -> PreSynthesisScores:
    values = {
        "intent_alignment": default,
        "must_answer_coverage": default,
        "source_relevance": default,
        "source_quality": default,
        "source_diversity": default,
        "source_recency": default,
        "grounding_consistency": default,
        "contradiction_handling": default,
        "synthesis_readiness": default,
    }
    values.update(overrides)
    return PreSynthesisScores(**values)


def _evaluation_run(
    *,
    active_ids: list[str],
    scores: PreSynthesisScores | None = None,
    affected_ids: list[str] | None = None,
    category: str = "coverage_gap",
    severity: str = "major",
) -> PreSynthesisEvaluationRun:
    issues: list[EvidenceIssue] = []
    directives: list[EvidenceRepairDirective] = []
    if affected_ids is not None:
        issues.append(
            EvidenceIssue(
                id="issue-1",
                category=category,
                severity=severity,
                description="The evaluated corpus requires repair.",
                affected_sub_question_ids=affected_ids,
                source_urls=[],
            )
        )
        directives.append(
            EvidenceRepairDirective(
                id="repair-1",
                issue_ids=["issue-1"],
                target_sub_question_ids=affected_ids,
                objective="Close the evaluated evidence gap.",
                suggested_queries=["replacement primary evidence"],
                acceptance_criteria=["The gap is covered by retained evidence."],
            )
        )

    evaluation = PreSynthesisEvaluation(
        schema_version="pre-synthesis-evaluation.v1",
        rubric_version="pre-synthesis-rubric.v1",
        assessed_sub_question_ids=active_ids,
        scores=scores or _scores(),
        issues=issues,
        repair_directives=directives,
        unresolved_questions=[],
        evaluation_limitations=[],
        summary="Deterministic controller fixture.",
    )
    return PreSynthesisEvaluationRun(
        status="completed",
        evaluation=evaluation,
        error_code=None,
        evaluator_model="test-evaluator",
        attempts=1,
        duration_ms=10,
    )


def _failed_evaluation_run() -> PreSynthesisEvaluationRun:
    return PreSynthesisEvaluationRun(
        status="failed",
        evaluation=None,
        error_code="provider_error",
        evaluator_model="test-evaluator",
        attempts=2,
        duration_ms=10,
    )


def _state(
    *,
    affected_ids: list[str] | None = None,
    scores: PreSynthesisScores | None = None,
    failed_run: bool = False,
) -> dict[str, Any]:
    active_ids = ["sq-a", "sq-b", "sq-c", "sq-d"]
    return {
        "topic": "Controller test topic",
        "sub_questions": [_sub_question(question_id) for question_id in active_ids],
        "sub_reports": [_sub_report(question_id) for question_id in active_ids],
        "failed_queries": [],
        "pre_synthesis_evaluation_run": (
            _failed_evaluation_run()
            if failed_run
            else _evaluation_run(
                active_ids=active_ids,
                affected_ids=affected_ids,
                scores=scores,
            )
        ),
        "plan_version": 1,
        "budget_snapshot": BudgetSnapshot(),
        "routing_history": [],
        "recovery_fingerprints": [],
    }


async def _controller_update(state: dict[str, Any]) -> dict[str, Any]:
    update = controller_module.controller_node(state)
    if inspect.isawaitable(update):
        update = await update
    assert isinstance(update, dict)
    return update


def _decision_value(update: dict[str, Any], field: str) -> Any:
    decision = update.get("controller_decision", update)
    if isinstance(decision, dict):
        return decision.get(field)
    return getattr(decision, field)


def _route(update: dict[str, Any]) -> str:
    route = _decision_value(update, "route")
    assert route in ROUTES
    return route


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("affected_ids", "failed_run", "expected_route"),
    [
        (None, False, "accept"),
        (["sq-a"], False, "targeted_repair"),
        (["sq-a", "sq-b"], False, "partial_replan"),
        (["sq-a", "sq-b", "sq-c"], False, "full_replan"),
        (None, True, "stop_incomplete"),
    ],
)
async def test_all_five_golden_routes(
    affected_ids: list[str] | None,
    failed_run: bool,
    expected_route: str,
) -> None:
    update = await _controller_update(
        _state(affected_ids=affected_ids, failed_run=failed_run)
    )

    assert _route(update) == expected_route


@pytest.mark.asyncio
async def test_accept_threshold_is_inclusive_at_85() -> None:
    update = await _controller_update(_state(scores=_scores(default=85)))

    assert _route(update) == "accept"


@pytest.mark.asyncio
async def test_documented_score_weights_control_the_85_boundary() -> None:
    # Weighted score is exactly 85.0 although the unweighted mean is lower.
    weighted_boundary = PreSynthesisScores(
        intent_alignment=80,
        must_answer_coverage=90,
        source_relevance=90,
        source_quality=90,
        source_diversity=70,
        source_recency=70,
        grounding_consistency=80,
        contradiction_handling=90,
        synthesis_readiness=90,
    )

    update = await _controller_update(_state(scores=weighted_boundary))

    assert _route(update) == "accept"
    assert _decision_value(update, "weighted_overall_score") == 85.0


@pytest.mark.asyncio
async def test_critical_threshold_is_inclusive_at_75() -> None:
    # Eight 87s plus one 75 keep the mean above 85 while exercising the exact
    # critical-dimension boundary independently.
    update = await _controller_update(
        _state(scores=_scores(default=87, grounding_consistency=75))
    )

    assert _route(update) == "accept"


@pytest.mark.asyncio
async def test_critical_score_below_75_cannot_accept() -> None:
    update = await _controller_update(
        _state(scores=_scores(default=100, grounding_consistency=74))
    )

    assert _route(update) != "accept"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("affected_ids", "expected_route"),
    [
        (["sq-a"], "targeted_repair"),  # exactly 25% weighted scope
        (["sq-a", "sq-b"], "partial_replan"),  # exactly 50%
        (["sq-a", "sq-b", "sq-c"], "full_replan"),  # above 50%
    ],
)
async def test_weighted_scope_boundaries_are_deterministic(
    affected_ids: list[str],
    expected_route: str,
) -> None:
    update = await _controller_update(_state(affected_ids=affected_ids))

    assert _route(update) == expected_route


@pytest.mark.asyncio
async def test_priority_weights_change_repair_scope() -> None:
    high_priority_state = _state(affected_ids=["sq-a"])
    high_priority_state["sub_questions"] = [
        _sub_question("sq-a", priority=1),
        _sub_question("sq-b", priority=3),
        _sub_question("sq-c", priority=3),
        _sub_question("sq-d", priority=3),
    ]
    low_priority_state = _state(affected_ids=["sq-d"])
    low_priority_state["sub_questions"] = list(high_priority_state["sub_questions"])

    high_update = await _controller_update(high_priority_state)
    low_update = await _controller_update(low_priority_state)

    assert _route(high_update) == "partial_replan"  # weight 3/6 = 50%
    assert _route(low_update) == "targeted_repair"  # weight 1/6


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_report_id", ["sq-a", "sq-d"])
async def test_missing_active_report_blocks_accept(missing_report_id: str) -> None:
    state = _state()
    state["sub_reports"] = [
        report
        for report in state["sub_reports"]
        if report.sub_question_id != missing_report_id
    ]

    update = await _controller_update(state)

    assert _route(update) != "accept"


@pytest.mark.asyncio
async def test_inactive_stale_report_blocks_accept() -> None:
    state = _state()
    state["sub_reports"].append(_sub_report("sq-stale"))

    update = await _controller_update(state)

    assert _route(update) != "accept"


@pytest.mark.asyncio
async def test_duplicate_active_report_blocks_accept() -> None:
    state = _state()
    state["sub_reports"].append(_sub_report("sq-a"))

    update = await _controller_update(state)

    assert _route(update) != "accept"


@pytest.mark.asyncio
async def test_stale_evaluation_for_previous_plan_blocks_accept() -> None:
    state = _state()
    run = state["pre_synthesis_evaluation_run"]
    state["pre_synthesis_evaluation_run"] = run.model_copy(
        update={
            "evaluation": run.evaluation.model_copy(
                update={"assessed_sub_question_ids": ["sq-a", "sq-b", "sq-c"]}
            )
        }
    )

    update = await _controller_update(state)

    assert _route(update) == "stop_incomplete"


def test_priority_zero_is_rejected_at_the_state_boundary() -> None:
    with pytest.raises(ValidationError):
        _sub_question("sq-invalid", priority=0)


@pytest.mark.asyncio
async def test_valid_dict_shaped_evaluation_and_budget_are_parsed() -> None:
    state = _state()
    state["pre_synthesis_evaluation_run"] = state[
        "pre_synthesis_evaluation_run"
    ].model_dump()
    state["budget_snapshot"] = state["budget_snapshot"].model_dump()

    update = await _controller_update(state)

    assert _route(update) == "accept"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state_field", "invalid_value", "expected_code"),
    [
        ("pre_synthesis_evaluation_run", {"status": "completed"}, "invalid_or_failed_evaluation"),
        ("budget_snapshot", {"pre_evaluations_used": "invalid"}, "invalid_controller_budget"),
    ],
)
async def test_invalid_dict_controller_inputs_stop_incomplete(
    state_field: str,
    invalid_value: dict[str, Any],
    expected_code: str,
) -> None:
    state = _state()
    state[state_field] = invalid_value

    update = await _controller_update(state)

    assert _route(update) == "stop_incomplete"
    assert _decision_value(update, "reason_code") == expected_code


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ["key_facts", "source_title"])
async def test_fingerprint_covers_every_evaluator_visible_report_field(
    changed_field: str,
) -> None:
    original = _state(affected_ids=["sq-a"])
    changed = _state(affected_ids=["sq-a"])
    report = changed["sub_reports"][0]
    if changed_field == "key_facts":
        changed["sub_reports"][0] = report.model_copy(
            update={"key_facts": ["Changed evaluator-visible fact."]}
        )
    else:
        changed_source = report.sources[0].model_copy(update={"title": "Changed title"})
        changed["sub_reports"][0] = report.model_copy(update={"sources": [changed_source]})

    original_update = await _controller_update(original)
    changed_update = await _controller_update(changed)

    assert _decision_value(original_update, "fingerprint") != _decision_value(
        changed_update,
        "fingerprint",
    )


@pytest.mark.asyncio
async def test_active_execution_failure_blocks_accept() -> None:
    state = _state()
    state["failed_queries"] = [
        {
            "sub_question_id": "sq-a",
            "query": ["primary evidence sq-a"],
            "error_code": "provider_error",
            "reason": "The provider could not complete this step.",
        }
    ]

    update = await _controller_update(state)

    assert _route(update) != "accept"


@pytest.mark.asyncio
async def test_second_targeted_repair_wave_is_still_allowed() -> None:
    state = _state(affected_ids=["sq-a"])
    state["budget_snapshot"] = BudgetSnapshot(
        targeted_repairs_used=1,
        total_recoveries_used=1,
    )

    update = await _controller_update(state)

    assert _route(update) == "targeted_repair"


@pytest.mark.asyncio
async def test_targeted_repair_budget_escalates_to_partial_replan() -> None:
    state = _state(affected_ids=["sq-a"])
    state["budget_snapshot"] = BudgetSnapshot(
        targeted_repairs_used=2,
        total_recoveries_used=2,
    )

    update = await _controller_update(state)

    assert _route(update) == "partial_replan"


@pytest.mark.asyncio
async def test_partial_replan_budget_escalates_to_full_replan() -> None:
    state = _state(affected_ids=["sq-a"])
    state["budget_snapshot"] = BudgetSnapshot(
        targeted_repairs_used=2,
        partial_replans_used=1,
        total_recoveries_used=3,
    )

    update = await _controller_update(state)

    assert _route(update) == "full_replan"


@pytest.mark.asyncio
async def test_full_replan_budget_stops_incomplete() -> None:
    state = _state(affected_ids=["sq-a"])
    state["budget_snapshot"] = BudgetSnapshot(
        targeted_repairs_used=2,
        partial_replans_used=1,
        full_replans_used=1,
        total_recoveries_used=4,
    )

    update = await _controller_update(state)

    assert _route(update) == "stop_incomplete"


@pytest.mark.asyncio
async def test_fourth_controller_cycle_is_allowed_but_fifth_is_not() -> None:
    fourth_cycle = _state(affected_ids=["sq-a"])
    fourth_cycle["budget_snapshot"] = BudgetSnapshot(
        targeted_repairs_used=1,
        partial_replans_used=1,
        full_replans_used=1,
        total_recoveries_used=3,
    )
    fifth_cycle = _state(affected_ids=["sq-a"])
    fifth_cycle["budget_snapshot"] = BudgetSnapshot(
        targeted_repairs_used=1,
        partial_replans_used=1,
        full_replans_used=1,
        total_recoveries_used=4,
    )

    fourth_update = await _controller_update(fourth_cycle)
    fifth_update = await _controller_update(fifth_cycle)

    assert _route(fourth_update) == "targeted_repair"
    assert _route(fifth_update) == "stop_incomplete"


@pytest.mark.asyncio
async def test_repeated_decision_fingerprint_escalates_without_repeating_work() -> None:
    state = _state(affected_ids=["sq-a"])
    first_update = await _controller_update(state)
    fingerprint = _decision_value(first_update, "fingerprint")
    assert isinstance(fingerprint, str) and fingerprint

    repeated_state = _state(affected_ids=["sq-a"])
    repeated_state["routing_history"] = first_update["routing_history"]
    repeated_state["recovery_fingerprints"] = first_update["recovery_fingerprints"]
    repeated_state["budget_snapshot"] = first_update["budget_snapshot"]
    repeated_update = await _controller_update(repeated_state)

    assert _route(repeated_update) == "partial_replan"
    assert _decision_value(repeated_update, "fingerprint") != fingerprint


@pytest.mark.asyncio
async def test_no_score_gain_escalates_even_when_corpus_fingerprint_changed() -> None:
    state = _state(affected_ids=["sq-a"])
    first_update = await _controller_update(state)

    no_gain_state = _state(affected_ids=["sq-a"])
    no_gain_state["sub_reports"][0] = no_gain_state["sub_reports"][0].model_copy(
        update={"findings": "A changed corpus that did not improve evaluator scores."}
    )
    no_gain_state["routing_history"] = first_update["routing_history"]
    no_gain_state["budget_snapshot"] = first_update["budget_snapshot"]
    update = await _controller_update(no_gain_state)

    assert _route(update) == "partial_replan"
    assert _decision_value(update, "reason_code") == "repeated_or_no_gain"
    assert _decision_value(update, "score_gain") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("affected_ids", "failed_run", "expected_counts"),
    [
        (None, False, (0, 0, 0, 0)),
        (["sq-a"], False, (1, 0, 0, 1)),
        (["sq-a", "sq-b"], False, (0, 1, 0, 1)),
        (["sq-a", "sq-b", "sq-c"], False, (0, 0, 1, 1)),
        (None, True, (0, 0, 0, 0)),
    ],
)
async def test_route_consumes_exactly_its_own_budget(
    affected_ids: list[str] | None,
    failed_run: bool,
    expected_counts: tuple[int, int, int, int],
) -> None:
    update = await _controller_update(
        _state(affected_ids=affected_ids, failed_run=failed_run)
    )
    budget = update["budget_snapshot"]

    assert budget.pre_evaluations_used == 1
    assert (
        budget.targeted_repairs_used,
        budget.partial_replans_used,
        budget.full_replans_used,
        budget.total_recoveries_used,
    ) == expected_counts


@pytest.mark.asyncio
async def test_fifth_pre_evaluation_is_allowed_but_sixth_is_not() -> None:
    fifth = _state()
    fifth["budget_snapshot"] = BudgetSnapshot(pre_evaluations_used=4)
    sixth = _state()
    sixth["budget_snapshot"] = BudgetSnapshot(pre_evaluations_used=5)

    fifth_update = await _controller_update(fifth)
    sixth_update = await _controller_update(sixth)

    assert _route(fifth_update) == "accept"
    assert fifth_update["budget_snapshot"].pre_evaluations_used == 5
    assert _route(sixth_update) == "stop_incomplete"
    assert sixth_update["budget_snapshot"].pre_evaluations_used == 5
