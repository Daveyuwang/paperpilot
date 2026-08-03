"""Acceptance-subject binding and deterministic post-repair routing contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from app.api import deep_research as deep_research_api
from app.deep_research.models import (
    AtomicClaimAudit,
    BudgetSnapshot,
    ClaimCitationAudit,
    ClaimEvidenceReference,
    PostSynthesisEvaluation,
    PostSynthesisEvaluationRun,
    PostSynthesisRoutingDecision,
    PostSynthesisScores,
    RepairStage,
    ReportEvaluationIssue,
    ReportSection,
    ReportSegmentAudit,
    ResearchReport,
    SourceRef,
    SubQuestion,
    SubReport,
)
from app.deep_research.nodes.controller import finalize_complete_node
from app.deep_research.nodes.post_controller import (
    decide_post_synthesis_route,
    route_after_post_controller,
)
from app.deep_research.provenance import (
    build_evidence_inventory,
    build_report_segments,
    evaluation_digest,
    report_digest,
)


_PRIORITIES = (1, 1, 1, 1, 2, 2, 3, 3)
_ALL_SCORES = {
    "intent_alignment": 95,
    "material_claim_grounding": 95,
    "citation_fidelity": 95,
    "citation_completeness": 95,
    "contradiction_handling": 95,
    "coverage": 95,
    "coherence": 95,
    "limitations_calibration": 95,
}


def _question(index: int, priority: int) -> SubQuestion:
    question_id = f"sq-{index}"
    return SubQuestion(
        id=question_id,
        question=f"What primary evidence answers {question_id}?",
        search_queries=[f"{question_id} primary evidence"],
        priority=priority,
        rationale="This branch is required by the research contract.",
    )


def _sub_report(question: SubQuestion) -> SubReport:
    return SubReport(
        sub_question_id=question.id,
        question=question.question,
        findings=f"The retained finding for {question.id} is narrowly supported.",
        key_facts=[f"The bounded fact for {question.id} is supported."],
        confidence=0.91,
        gaps="The evidence does not establish universal generalization.",
        sources=[
            SourceRef(
                url=f"https://evidence.example/{question.id}",
                title=f"Primary source for {question.id}",
                excerpt=f"Direct evidence excerpt retained for {question.id}.",
                published_at="2026-07-01",
                source_type="primary_document",
            )
        ],
    )


def _candidate_report(reports: list[SubReport], *, marker: str = "current") -> ResearchReport:
    sources, evidence = build_evidence_inventory(reports)
    direct = next(unit for unit in evidence if unit.provenance == "source_excerpt")
    citation = f"[E:{direct.evidence_id}] [S:{direct.source_ids[0]}]"
    return ResearchReport(
        title=f"Bounded {marker} report",
        executive_summary=f"The executive conclusion is bounded. {citation}",
        sections=[
            ReportSection(
                heading="Evidence",
                content=f"The retained evidence supports this section. {citation}",
            )
        ],
        key_findings=[f"One calibrated finding follows. {citation}"],
        limitations="The retained corpus is limited in scope.",
        sources=[
            SourceRef(
                source_id=source.source_id,
                url=source.url,
                title=source.title,
                published_at=source.published_at,
                source_type=source.source_type,
            )
            for source in sources
        ],
    )


def _base_state() -> dict[str, Any]:
    questions = [
        _question(index, priority)
        for index, priority in enumerate(_PRIORITIES, start=1)
    ]
    reports = [_sub_report(question) for question in questions]
    return {
        "topic": "Acceptance binding fixture",
        "sub_questions": questions,
        "sub_reports": reports,
        "candidate_report": _candidate_report(reports),
        "final_report": None,
        "report_version": 7,
        "report_accepted": False,
        "post_evaluation_history": [],
        "post_synthesis_controller_decision": None,
        "post_routing_history": [],
        "post_recovery_fingerprints": [],
        "target_report_segment_ids": [],
        "budget_snapshot": BudgetSnapshot(),
        "plan_version": 3,
    }


def _audits(
    state: dict[str, Any],
    *,
    defect: str | None = None,
) -> list[ReportSegmentAudit]:
    _, evidence = build_evidence_inventory(state["sub_reports"])
    direct = next(unit for unit in evidence if unit.provenance == "source_excerpt")
    audits: list[ReportSegmentAudit] = []
    material_index = 0
    for segment in build_report_segments(state["candidate_report"]):
        if segment.component not in {"executive_summary", "section", "key_finding"}:
            audits.append(
                ReportSegmentAudit(
                    segment_id=segment.id,
                    contains_material_claims=False,
                    claims=[],
                )
            )
            continue

        claim_id = f"claim-material-{material_index:03d}"
        is_defective_claim = material_index == 0 and defect == "evidence"
        is_overstated_claim = material_index == 0 and defect == "synthesis"
        material_index += 1
        audits.append(
            ReportSegmentAudit(
                segment_id=segment.id,
                contains_material_claims=True,
                claims=[
                    AtomicClaimAudit(
                        claim_id=claim_id,
                        claim_text=segment.text,
                        materiality="critical",
                        support="unsupported" if is_defective_claim else "supported",
                        evidence_refs=(
                            []
                            if is_defective_claim
                            else [
                                ClaimEvidenceReference(
                                    evidence_id=direct.evidence_id,
                                    supporting_excerpt=direct.text,
                                )
                            ]
                        ),
                        citation=ClaimCitationAudit(
                            status="missing" if is_defective_claim else "correct",
                            cited_source_ids=(
                                [] if is_defective_claim else [direct.source_ids[0]]
                            ),
                            rationale="The citation state is explicitly audited.",
                        ),
                        calibration="overstated" if is_overstated_claim else "accurate",
                        rationale="The claim was checked against retained evidence.",
                    )
                ],
            )
        )
    return audits


def _issue(
    state: dict[str, Any],
    *,
    category: str,
    stage: str,
    affected_ids: list[str],
) -> ReportEvaluationIssue:
    segment_id = next(
        segment.id
        for segment in build_report_segments(state["candidate_report"])
        if segment.component == "executive_summary"
    )
    return ReportEvaluationIssue(
        id=f"issue-{category}",
        category=category,
        severity="blocker" if category == "contract_violation" else "major",
        claim_ids=["claim-material-000"],
        segment_ids=[segment_id],
        affected_sub_question_ids=affected_ids,
        suggested_repair_stage=stage,
        suggested_queries=["bounded repair query"] if stage == "evidence" else [],
        description=f"The post-synthesis audit found {category}.",
        acceptance_criteria=["The identified defect must be resolved."],
    )


def _completed_run(
    state: dict[str, Any],
    *,
    issues: list[ReportEvaluationIssue] | None = None,
    defect: str | None = None,
    bound_digest: str | None = None,
    bound_version: int | None = None,
) -> PostSynthesisEvaluationRun:
    report = state["candidate_report"]
    return PostSynthesisEvaluationRun(
        status="completed",
        evaluation=PostSynthesisEvaluation(
            schema_version="post-synthesis-eval.v1",
            rubric_version="report-quality.v1",
            segment_audits=_audits(state, defect=defect),
            scores=PostSynthesisScores(**_ALL_SCORES),
            issues=issues or [],
            unresolved_questions=[],
            summary="Every report surface was audited against retained evidence.",
        ),
        error_code=None,
        report_digest=bound_digest or report_digest(report),
        report_version=(
            state["report_version"] if bound_version is None else bound_version
        ),
        evaluator_model="post-acceptance-test-evaluator",
        attempts=1,
        duration_ms=5,
    )


def _state_with_issue(
    *,
    category: str,
    stage: str,
    affected_ids: list[str],
    budget: BudgetSnapshot | None = None,
) -> dict[str, Any]:
    state = _base_state()
    issue = _issue(
        state,
        category=category,
        stage=stage,
        affected_ids=affected_ids,
    )
    defect = "evidence" if stage == "evidence" else "synthesis" if stage == "synthesis" else None
    state["post_synthesis_evaluation_run"] = _completed_run(
        state,
        issues=[issue],
        defect=defect,
    )
    if budget is not None:
        state["budget_snapshot"] = budget
    return state


def _accepted_state() -> dict[str, Any]:
    state = _base_state()
    state["post_synthesis_evaluation_run"] = _completed_run(state)
    return state


def _checkpoint_state(state: dict[str, Any]) -> dict[str, Any]:
    checkpoint = deepcopy(state)
    for key in ("sub_questions", "sub_reports"):
        checkpoint[key] = [item.model_dump(mode="json") for item in state[key]]
    checkpoint["candidate_report"] = state["candidate_report"].model_dump(mode="json")
    checkpoint["post_synthesis_evaluation_run"] = state[
        "post_synthesis_evaluation_run"
    ].model_dump(mode="json")
    checkpoint["budget_snapshot"] = state["budget_snapshot"].model_dump(mode="json")
    return checkpoint


@pytest.mark.parametrize(
    "omitted_fields",
    [
        ("report_digest",),
        ("report_version",),
        ("report_digest", "report_version"),
    ],
)
def test_completed_post_evaluation_requires_both_report_bindings(
    omitted_fields: tuple[str, ...],
) -> None:
    payload = _completed_run(_base_state()).model_dump(mode="json")
    for field in omitted_fields:
        payload.pop(field)

    with pytest.raises(ValidationError):
        PostSynthesisEvaluationRun.model_validate(payload)


def test_failed_post_evaluation_may_have_no_report_binding() -> None:
    run = PostSynthesisEvaluationRun(
        status="failed",
        evaluation=None,
        error_code="provider_error",
        evaluator_model="post-acceptance-test-evaluator",
        attempts=2,
        duration_ms=5,
    )

    assert run.report_digest is None
    assert run.report_version is None


def test_typed_evaluation_and_accept_decision_bind_the_exact_candidate() -> None:
    state = _accepted_state()

    decision = decide_post_synthesis_route(state)

    assert decision.route == "accept"
    assert decision.report_digest == report_digest(state["candidate_report"])
    assert decision.report_version == state["report_version"]
    assert decision.evaluation_digest == evaluation_digest(
        state["post_synthesis_evaluation_run"]
    )


def test_accept_decision_requires_an_evaluation_binding() -> None:
    state = _accepted_state()
    payload = decide_post_synthesis_route(state).model_dump(mode="json")
    payload.pop("evaluation_digest")

    with pytest.raises(ValidationError):
        PostSynthesisRoutingDecision.model_validate(payload)


@pytest.mark.parametrize("stale_subject", ["digest", "version"])
def test_typed_stale_evaluation_subject_fails_closed(stale_subject: str) -> None:
    state = _accepted_state()
    if stale_subject == "digest":
        state["candidate_report"] = state["candidate_report"].model_copy(
            update={"title": "A newer candidate report"}
        )
    else:
        state["report_version"] += 1

    decision = decide_post_synthesis_route(state)

    assert decision.route == "stop_incomplete"
    assert decision.reason_code == "invalid_or_failed_post_evaluation"


def test_valid_checkpoint_subject_revalidates_and_remains_bound() -> None:
    state = _accepted_state()
    checkpoint = _checkpoint_state(state)

    decision = decide_post_synthesis_route(checkpoint)

    assert decision.route == "accept"
    assert decision.report_digest == report_digest(state["candidate_report"])
    assert decision.report_version == state["report_version"]


@pytest.mark.parametrize(
    ("field", "mutation"),
    [
        ("report_digest", "missing"),
        ("report_version", "missing"),
        ("report_digest", "mismatch"),
        ("report_version", "mismatch"),
    ],
)
def test_checkpoint_missing_or_mismatched_subject_fails_closed(
    field: str,
    mutation: str,
) -> None:
    checkpoint = _checkpoint_state(_accepted_state())
    run = checkpoint["post_synthesis_evaluation_run"]
    if mutation == "missing":
        run.pop(field)
    elif field == "report_digest":
        run[field] = "0" * 64
    else:
        run[field] += 1

    decision = decide_post_synthesis_route(checkpoint)

    assert decision.route == "stop_incomplete"


def test_checkpoint_stale_decision_cannot_cross_the_controller_edge() -> None:
    state = _accepted_state()
    decision = decide_post_synthesis_route(state)
    checkpoint = _checkpoint_state(state)
    checkpoint["post_synthesis_controller_decision"] = decision.model_dump(
        mode="json"
    )
    checkpoint["candidate_report"]["title"] = "A candidate changed after routing"

    assert route_after_post_controller(checkpoint) == "stop_incomplete"


@pytest.mark.parametrize(
    ("decision_shape", "run_shape"),
    [("typed", "checkpoint"), ("checkpoint", "typed")],
)
def test_old_accept_decision_cannot_pair_with_new_failing_evaluation(
    decision_shape: str,
    run_shape: str,
) -> None:
    state = _accepted_state()
    accepted = decide_post_synthesis_route(state)
    issue = _issue(
        state,
        category="unsupported_claim",
        stage="evidence",
        affected_ids=["sq-1"],
    )
    newer_failing_run = _completed_run(
        state,
        issues=[issue],
        defect="evidence",
    )
    assert evaluation_digest(newer_failing_run) != accepted.evaluation_digest

    state["post_synthesis_controller_decision"] = (
        accepted.model_dump(mode="json")
        if decision_shape == "checkpoint"
        else accepted
    )
    state["post_synthesis_evaluation_run"] = (
        newer_failing_run.model_dump(mode="json")
        if run_shape == "checkpoint"
        else newer_failing_run
    )

    assert route_after_post_controller(state) == "stop_incomplete"
    assert finalize_complete_node(state)["terminal_status"] == "incomplete"
    assert deep_research_api._publishable_report(
        {
            **state,
            "terminal_status": "completed",
            "report_accepted": True,
            "final_report": state["candidate_report"],
        }
    ) is None


def test_old_failing_decision_cannot_control_new_accepted_evaluation() -> None:
    state = _state_with_issue(
        category="unsupported_claim",
        stage="evidence",
        affected_ids=["sq-1"],
    )
    old_repair_decision = decide_post_synthesis_route(state)
    assert old_repair_decision.route == "targeted_repair"
    state["post_synthesis_controller_decision"] = (
        old_repair_decision.model_dump(mode="json")
    )
    state["post_synthesis_evaluation_run"] = _completed_run(state)

    assert route_after_post_controller(state) == "stop_incomplete"


@pytest.mark.parametrize("decision_shape", ["typed", "checkpoint"])
def test_forged_accept_cannot_override_bound_failing_evaluation(
    decision_shape: str,
) -> None:
    state = _state_with_issue(
        category="unsupported_claim",
        stage="evidence",
        affected_ids=["sq-1"],
    )
    genuine = decide_post_synthesis_route(state)
    assert genuine.route == "targeted_repair"
    forged: PostSynthesisRoutingDecision | dict[str, Any] = genuine.model_copy(
        update={
            "route": "accept",
            "repair_stage": RepairStage.INITIAL,
            "reason_code": "post_quality_gate_passed",
            "reason": "forged acceptance",
        }
    )
    if decision_shape == "checkpoint":
        forged = forged.model_dump(mode="json")
    state["post_synthesis_controller_decision"] = forged
    state["controller_decision"] = forged

    assert route_after_post_controller(state) == "stop_incomplete"
    final_update = finalize_complete_node(state)
    assert final_update["terminal_status"] == "incomplete"
    assert final_update["report_accepted"] is False
    assert final_update["final_report"] is None

    pretend_published = {
        **state,
        "terminal_status": "completed",
        "report_accepted": True,
        "final_report": state["candidate_report"],
    }
    assert deep_research_api._publishable_report(pretend_published) is None


@pytest.mark.parametrize("decision_shape", ["typed", "checkpoint"])
@pytest.mark.parametrize("mismatch", ["digest", "version"])
def test_finalizer_and_api_reject_mismatched_acceptance_subject(
    decision_shape: str,
    mismatch: str,
) -> None:
    state = _accepted_state()
    accepted = decide_post_synthesis_route(state)
    assert accepted.route == "accept"
    bad_update = (
        {"report_digest": "0" * 64}
        if mismatch == "digest"
        else {"report_version": state["report_version"] + 1}
    )
    bad_decision: PostSynthesisRoutingDecision | dict[str, Any] = accepted.model_copy(
        update=bad_update
    )
    if decision_shape == "checkpoint":
        bad_decision = bad_decision.model_dump(mode="json")
    state["post_synthesis_controller_decision"] = bad_decision
    state["controller_decision"] = bad_decision

    final_update = finalize_complete_node(state)

    assert final_update["terminal_status"] == "incomplete"
    assert final_update["final_report"] is None
    assert final_update["report_accepted"] is False

    pretend_published = {
        **state,
        "terminal_status": "completed",
        "report_accepted": True,
        "final_report": state["candidate_report"],
    }
    assert deep_research_api._publishable_report(pretend_published) is None


@pytest.mark.parametrize("mutation", ["candidate", "final", "report_version"])
def test_finalizer_and_api_recompute_the_published_report_subject(
    mutation: str,
) -> None:
    state = _accepted_state()
    accepted = decide_post_synthesis_route(state)
    state["post_synthesis_controller_decision"] = accepted
    state["controller_decision"] = accepted
    if mutation == "candidate":
        state["candidate_report"] = state["candidate_report"].model_copy(
            update={"title": "A different candidate after acceptance"}
        )
    elif mutation == "final":
        state["final_report"] = state["candidate_report"].model_copy(
            update={"title": "A different final report"}
        )
    else:
        state["report_version"] += 1

    final_update = finalize_complete_node(state)

    assert final_update["terminal_status"] == "incomplete"
    assert final_update["final_report"] is None
    assert final_update["report_accepted"] is False

    api_state = {
        **state,
        "terminal_status": "completed",
        "report_accepted": True,
        "final_report": state.get("final_report") or state["candidate_report"],
    }
    assert deep_research_api._publishable_report(api_state) is None


@pytest.mark.parametrize(
    ("affected_ids", "expected_route", "expected_stage"),
    [
        (["sq-1"], "targeted_repair", RepairStage.EVIDENCE),
        (["sq-7", "sq-8"], "targeted_repair", RepairStage.EVIDENCE),
        (["sq-1", "sq-7", "sq-8"], "partial_replan", RepairStage.PARTIAL_REPLAN),
        (
            ["sq-1", "sq-2", "sq-3", "sq-4"],
            "full_replan",
            RepairStage.FULL_REPLAN,
        ),
    ],
)
def test_evidence_scope_selects_targeted_partial_or_full_recovery(
    affected_ids: list[str],
    expected_route: str,
    expected_stage: RepairStage,
) -> None:
    state = _state_with_issue(
        category="unsupported_claim",
        stage="evidence",
        affected_ids=affected_ids,
    )

    decision = decide_post_synthesis_route(state)

    assert decision.route == expected_route
    assert decision.repair_stage == expected_stage


def test_one_high_weight_affected_branch_forces_full_replan() -> None:
    state = _base_state()
    state["sub_questions"] = [
        question
        for question in state["sub_questions"]
        if question.id in {"sq-1", "sq-8"}
    ]
    state["sub_reports"] = [
        report
        for report in state["sub_reports"]
        if report.sub_question_id in {"sq-1", "sq-8"}
    ]
    state["candidate_report"] = _candidate_report(state["sub_reports"])
    issue = _issue(
        state,
        category="unsupported_claim",
        stage="evidence",
        affected_ids=["sq-1"],
    )
    state["post_synthesis_evaluation_run"] = _completed_run(
        state,
        issues=[issue],
        defect="evidence",
    )

    decision = decide_post_synthesis_route(state)

    assert decision.affected_priority_ratio == 1.0
    assert decision.route == "full_replan"
    assert decision.repair_stage == RepairStage.FULL_REPLAN


@pytest.mark.parametrize(
    ("category", "affected_ids", "expected_route"),
    [
        ("contract_violation", ["sq-8"], "full_replan"),
        ("coverage_gap", ["sq-1", "sq-2", "sq-3", "sq-4"], "full_replan"),
        ("coverage_gap", ["sq-1", "sq-8"], "partial_replan"),
    ],
)
def test_plan_scope_uses_contract_or_majority_for_full_replan(
    category: str,
    affected_ids: list[str],
    expected_route: str,
) -> None:
    state = _state_with_issue(
        category=category,
        stage="plan",
        affected_ids=affected_ids,
    )

    decision = decide_post_synthesis_route(state)

    assert decision.route == expected_route


def test_exhausted_targeted_budget_escalates_to_partial_replan() -> None:
    state = _state_with_issue(
        category="unsupported_claim",
        stage="evidence",
        affected_ids=["sq-1"],
        budget=BudgetSnapshot(targeted_repairs_used=2, targeted_repair_limit=2),
    )

    decision = decide_post_synthesis_route(state)

    assert decision.route == "partial_replan"
    assert decision.repair_stage == RepairStage.PARTIAL_REPLAN


def test_exhausted_targeted_and_partial_budgets_escalate_to_full_replan() -> None:
    state = _state_with_issue(
        category="unsupported_claim",
        stage="evidence",
        affected_ids=["sq-1"],
        budget=BudgetSnapshot(
            targeted_repairs_used=2,
            targeted_repair_limit=2,
            partial_replans_used=1,
            partial_replan_limit=1,
        ),
    )

    decision = decide_post_synthesis_route(state)

    assert decision.route == "full_replan"
    assert decision.repair_stage == RepairStage.FULL_REPLAN


def test_exhausted_partial_budget_escalates_scoped_replan_to_full() -> None:
    state = _state_with_issue(
        category="unsupported_claim",
        stage="evidence",
        affected_ids=["sq-1", "sq-7", "sq-8"],
        budget=BudgetSnapshot(partial_replans_used=1, partial_replan_limit=1),
    )

    decision = decide_post_synthesis_route(state)

    assert decision.route == "full_replan"
    assert decision.repair_stage == RepairStage.FULL_REPLAN


@pytest.mark.parametrize(
    "budget",
    [
        BudgetSnapshot(total_recoveries_used=4, total_recovery_limit=4),
        BudgetSnapshot(full_replans_used=1, full_replan_limit=1),
    ],
)
def test_total_or_full_replan_exhaustion_stops_incomplete(
    budget: BudgetSnapshot,
) -> None:
    state = _state_with_issue(
        category="unsupported_claim",
        stage="evidence",
        affected_ids=["sq-1", "sq-2", "sq-3", "sq-4"],
        budget=budget,
    )

    decision = decide_post_synthesis_route(state)

    assert decision.route == "stop_incomplete"


def test_synthesis_repair_exhaustion_stops_incomplete() -> None:
    state = _state_with_issue(
        category="overstatement",
        stage="synthesis",
        affected_ids=[],
        budget=BudgetSnapshot(synthesis_repairs_used=2, synthesis_repair_limit=2),
    )

    decision = decide_post_synthesis_route(state)

    assert decision.route == "stop_incomplete"
