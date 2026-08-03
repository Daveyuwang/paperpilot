"""Focused tests for the post-synthesis claim/evidence quality gate."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

import pytest

from app.deep_research.models import (
    AtomicClaimAudit,
    BudgetSnapshot,
    ClaimCitationAudit,
    ClaimEvidenceReference,
    PostSynthesisEvaluation,
    PostSynthesisEvaluationRun,
    PostSynthesisScores,
    ReportEvaluationIssue,
    ReportSection,
    ReportSegmentAudit,
    ResearchReport,
    SourceRef,
    SubQuestion,
    SubReport,
)
from app.deep_research.nodes import evaluate_report as evaluate_report_module
from app.deep_research.nodes import post_controller as post_controller_module
from app.deep_research.provenance import build_evidence_inventory, report_digest


class _FakeStructuredLLM:
    def __init__(self, response: Any = None, error: BaseException | None = None):
        self.response = response
        self.error = error
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any) -> Any:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.response


def _question(question_id: str) -> SubQuestion:
    return SubQuestion(
        id=question_id,
        question=f"What is known about {question_id}?",
        search_queries=[f"{question_id} primary evidence"],
        priority=3 if question_id == "sq-a" else 1,
        rationale="Required by the research objective.",
    )


def _source(question_id: str) -> SourceRef:
    return SourceRef(
        url=(
            f"https://evidence.example/{question_id}"
            "?view=full&access_token=fixture-secret"
        ),
        title=f"Primary evidence {question_id}",
        excerpt=(
            f"Direct source excerpt for {question_id} supports the calibrated "
            "material conclusion."
        ),
        published_at="2026-07-01",
        source_type="primary_document",
    )


def _sub_report(question_id: str) -> SubReport:
    return SubReport(
        sub_question_id=question_id,
        question=f"What is known about {question_id}?",
        findings=f"Retained finding for {question_id} supports the calibrated claim.",
        key_facts=[f"Material fact for {question_id}."],
        confidence=0.9,
        gaps="No known material gap.",
        sources=[_source(question_id)],
    )


def _report(*, failed_section: bool = False) -> ResearchReport:
    sources, evidence = build_evidence_inventory(
        [_sub_report("sq-a"), _sub_report("sq-b")]
    )
    direct_unit = next(
        unit for unit in evidence if unit.provenance == "source_excerpt"
    )
    marker = f"[E:{direct_unit.evidence_id}] [S:{direct_unit.source_ids[0]}]"
    section_content = (
        "*Section generation failed: provider unavailable*"
        if failed_section
        else f"Section evidence supports the calibrated material conclusion. {marker}"
    )
    return ResearchReport(
        title="A Calibrated Research Report",
        executive_summary=f"Executive summary grounded in direct evidence. {marker}",
        sections=[
            ReportSection(heading="Evidence", content=section_content),
            ReportSection(
                heading="Implications",
                content=f"Bounded implications follow from direct evidence. {marker}",
            ),
        ],
        key_findings=[
            f"First grounded key finding. {marker}",
            f"Second grounded key finding. {marker}",
        ],
        limitations="The retained corpus does not establish universal generalization.",
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


def _state(*, failed_section: bool = False) -> dict[str, Any]:
    return {
        "topic": "Post-synthesis evaluator test topic",
        "depth": "deep",
        "api_key": "POST-EVAL-SECRET-KEY",
        "llm_base_url": "https://api.deepseek.com/v1",
        "llm_model": "deepseek-reasoner",
        "sub_questions": [_question("sq-a"), _question("sq-b")],
        "sub_reports": [_sub_report("sq-a"), _sub_report("sq-b")],
        "candidate_report": _report(failed_section=failed_section),
        "final_report": None,
        "report_accepted": False,
        "post_evaluation_history": [],
        "post_synthesis_controller_decision": None,
        "post_routing_history": [],
        "post_recovery_fingerprints": [],
        "target_report_segment_ids": [],
        "report_revision_count": 0,
        "report_revision_status": None,
        "report_version": 1,
        "budget_snapshot": BudgetSnapshot(),
        "routing_history": [],
        "recovery_fingerprints": [],
        "plan_version": 1,
    }


def _report_subject(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "report_digest": report_digest(state["candidate_report"]),
        "report_version": state["report_version"],
    }


def _scores(**overrides: int) -> PostSynthesisScores:
    values = {
        "intent_alignment": 95,
        "material_claim_grounding": 95,
        "citation_fidelity": 95,
        "citation_completeness": 95,
        "contradiction_handling": 95,
        "coverage": 95,
        "coherence": 95,
        "limitations_calibration": 95,
    }
    values.update(overrides)
    return PostSynthesisScores(**values)


def _segments(state: dict[str, Any]) -> list[Any]:
    return evaluate_report_module.build_report_segments(state["candidate_report"])


def _segment_audits(
    state: dict[str, Any],
    *,
    claim: AtomicClaimAudit | None = None,
) -> list[ReportSegmentAudit]:
    segments = _segments(state)
    audits = []
    for segment in segments:
        claims = (
            [claim.model_copy(update={"claim_text": segment.text})]
            if claim is not None and segment.component == "executive_summary"
            else []
        )
        audits.append(
            ReportSegmentAudit(
                segment_id=segment.id,
                contains_material_claims=bool(claims),
                claims=claims,
            )
        )
    return audits


def _evaluation(
    state: dict[str, Any],
    *,
    audits: list[ReportSegmentAudit] | None = None,
    issues: list[ReportEvaluationIssue] | None = None,
    scores: PostSynthesisScores | None = None,
) -> PostSynthesisEvaluation:
    return PostSynthesisEvaluation(
        schema_version="post-synthesis-eval.v1",
        rubric_version="report-quality.v1",
        segment_audits=audits if audits is not None else _segment_audits(state),
        scores=scores or _scores(),
        issues=issues or [],
        unresolved_questions=[],
        summary="Every supplied report surface was audited against retained evidence.",
    )


def _patch_llm(
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeStructuredLLM,
) -> list[dict[str, Any]]:
    factory_calls: list[dict[str, Any]] = []

    def fake_factory(state, schema, **kwargs):
        factory_calls.append({"state": state, "schema": schema, "kwargs": kwargs})
        return fake

    monkeypatch.setattr(evaluate_report_module, "make_structured_llm", fake_factory)
    return factory_calls


@pytest.mark.asyncio
async def test_llm_receives_every_report_surface_and_evidence_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    fake = _FakeStructuredLLM(response=_evaluation(state))
    factory_calls = _patch_llm(monkeypatch, fake)

    update = await evaluate_report_module.post_synthesis_evaluate_node(state)

    run = update["post_synthesis_evaluation_run"]
    assert isinstance(run, PostSynthesisEvaluationRun)
    assert run.status == "completed"
    assert run.evaluation is not None
    assert run.report_digest == report_digest(state["candidate_report"])
    assert run.report_version == state["report_version"]
    assert factory_calls[0]["schema"] is PostSynthesisEvaluation
    assert factory_calls[0]["kwargs"]["temperature"] == 0.0

    prompt = json.dumps(fake.calls, default=str, ensure_ascii=False)
    report = state["candidate_report"]
    assert report.title in prompt
    assert report.executive_summary in prompt
    assert all(section.heading in prompt and section.content in prompt for section in report.sections)
    assert all(finding in prompt for finding in report.key_findings)
    assert report.limitations in prompt
    assert all(source.url in prompt for source in report.sources)
    assert all(question.id in prompt for question in state["sub_questions"])
    assert "evidence_id" in prompt and "source_id" in prompt
    assert "POST-EVAL-SECRET-KEY" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_mutation", ["missing", "duplicate", "unknown"])
async def test_segment_audit_set_must_exactly_match_candidate_report(
    monkeypatch: pytest.MonkeyPatch,
    audit_mutation: str,
) -> None:
    state = _state()
    audits = _segment_audits(state)
    if audit_mutation == "missing":
        audits = audits[:-1]
    elif audit_mutation == "duplicate":
        audits = [*audits, deepcopy(audits[0])]
    else:
        audits[0] = audits[0].model_copy(update={"segment_id": "seg-unknown"})
    fake = _FakeStructuredLLM(response=_evaluation(state, audits=audits))
    _patch_llm(monkeypatch, fake)

    update = await evaluate_report_module.post_synthesis_evaluate_node(state)

    run = update["post_synthesis_evaluation_run"]
    assert run.status == "failed"
    assert run.evaluation is None
    assert run.error_code in {"invalid_output", "invalid_references"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [asyncio.TimeoutError(), {"schema_version": "bad"}])
async def test_timeout_or_malformed_output_fails_closed_and_clears_stale_success(
    monkeypatch: pytest.MonkeyPatch,
    failure: Any,
) -> None:
    state = _state()
    state["post_synthesis_evaluation_run"] = PostSynthesisEvaluationRun(
        status="completed",
        evaluation=_evaluation(state),
        error_code=None,
        **_report_subject(state),
        evaluator_model="stale-model",
        attempts=1,
        duration_ms=2,
    )
    fake = (
        _FakeStructuredLLM(error=failure)
        if isinstance(failure, BaseException)
        else _FakeStructuredLLM(response=failure)
    )
    _patch_llm(monkeypatch, fake)

    update = await evaluate_report_module.post_synthesis_evaluate_node(state)

    run = update["post_synthesis_evaluation_run"]
    assert run.status == "failed"
    assert run.evaluation is None
    assert run.error_code in {"timeout", "invalid_output"}
    assert run.report_digest == report_digest(state["candidate_report"])
    assert run.report_version == state["report_version"]
    assert post_controller_module.decide_post_synthesis_route(
        {**state, **update}
    ).route == "stop_incomplete"


def test_failed_section_placeholder_blocks_accept() -> None:
    state = _state(failed_section=True)
    run = PostSynthesisEvaluationRun(
        status="completed",
        evaluation=_evaluation(state),
        error_code=None,
        **_report_subject(state),
        evaluator_model="test-evaluator",
        attempts=1,
        duration_ms=1,
    )

    decision = post_controller_module.decide_post_synthesis_route(
        {**state, "post_synthesis_evaluation_run": run}
    )

    assert decision.route != "accept"


@pytest.mark.asyncio
async def test_failed_section_placeholder_fails_before_calling_evaluator_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(failed_section=True)

    def forbidden_factory(*_args, **_kwargs):
        raise AssertionError("a failed synthesis section must fail before model invocation")

    monkeypatch.setattr(
        evaluate_report_module,
        "make_structured_llm",
        forbidden_factory,
    )

    update = await evaluate_report_module.post_synthesis_evaluate_node(state)

    run = update["post_synthesis_evaluation_run"]
    assert run.status == "failed"
    assert run.error_code == "section_generation_failure"
    assert run.attempts == 0
    assert state["candidate_report"] is not None
    assert state["final_report"] is None


@pytest.mark.asyncio
async def test_exhausted_post_evaluation_budget_skips_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    state["budget_snapshot"] = BudgetSnapshot(
        post_evaluations_used=1,
        post_evaluation_limit=1,
    )

    def forbidden_factory(*_args, **_kwargs):
        raise AssertionError("an exhausted evaluator budget must not invoke the LLM")

    monkeypatch.setattr(
        evaluate_report_module,
        "make_structured_llm",
        forbidden_factory,
    )

    update = await evaluate_report_module.post_synthesis_evaluate_node(state)

    run = update["post_synthesis_evaluation_run"]
    assert run.status == "failed"
    assert run.error_code == "budget_exhausted"
    assert run.attempts == 0
    assert run.report_digest == report_digest(state["candidate_report"])
    assert run.report_version == state["report_version"]


def _unsupported_claim(
    state: dict[str, Any],
    *,
    support: str = "unsupported",
    calibration: str = "accurate",
) -> AtomicClaimAudit:
    return AtomicClaimAudit(
        claim_id="claim-material",
        claim_text=next(
            segment.text
            for segment in _segments(state)
            if segment.component == "executive_summary"
        ),
        materiality="critical",
        support=support,
        evidence_refs=[],
        citation=ClaimCitationAudit(
            status="missing",
            cited_source_ids=[],
            rationale="No retained source directly supports this wording.",
        ),
        calibration=calibration,
        rationale="The evidence does not entail this material claim.",
    )


def _supported_claim(state: dict[str, Any]) -> AtomicClaimAudit:
    sources, evidence = build_evidence_inventory(state["sub_reports"])
    unit = next(
        item for item in evidence if item.provenance == "source_excerpt"
    )
    assert sources and unit.source_ids
    segment = next(
        item
        for item in _segments(state)
        if item.component == "executive_summary"
    )
    return AtomicClaimAudit(
        claim_id="claim-supported",
        claim_text=segment.text,
        materiality="critical",
        support="supported",
        evidence_refs=[
            ClaimEvidenceReference(
                evidence_id=unit.evidence_id,
                supporting_excerpt=unit.text,
            )
        ],
        citation=ClaimCitationAudit(
            status="correct",
            cited_source_ids=[unit.source_ids[0]],
            rationale="The retained source and evidence directly support this wording.",
        ),
        calibration="accurate",
        rationale="The wording stays within the retained evidence boundary.",
    )


def _controller_segment_audits(
    state: dict[str, Any],
    *,
    first_claim: AtomicClaimAudit,
) -> list[ReportSegmentAudit]:
    baseline = _supported_claim(state)
    audits: list[ReportSegmentAudit] = []
    substantive_index = 0
    for index, segment in enumerate(_segments(state)):
        if segment.component not in {"executive_summary", "section", "key_finding"}:
            audits.append(
                ReportSegmentAudit(
                    segment_id=segment.id,
                    contains_material_claims=False,
                    claims=[],
                )
            )
            continue
        claim = (
            first_claim.model_copy(update={"claim_text": segment.text})
            if substantive_index == 0
            else baseline.model_copy(
                update={
                    "claim_id": f"claim-supported-{index:03d}",
                    "claim_text": segment.text,
                }
            )
        )
        substantive_index += 1
        audits.append(
            ReportSegmentAudit(
                segment_id=segment.id,
                contains_material_claims=True,
                claims=[claim],
            )
        )
    return audits


def _issue(
    state: dict[str, Any],
    *,
    issue_id: str,
    category: str,
    stage: str,
    target_ids: list[str],
    severity: str = "major",
) -> ReportEvaluationIssue:
    return ReportEvaluationIssue(
        id=issue_id,
        category=category,
        severity=severity,
        claim_ids=["claim-supported"],
        segment_ids=[
            next(
                segment.id
                for segment in _segments(state)
                if segment.component == "executive_summary"
            )
        ],
        affected_sub_question_ids=target_ids,
        suggested_repair_stage=stage,
        description=f"{category} fixture.",
        acceptance_criteria=[f"Resolve {category} before publication."],
    )


def _completed_post_run(
    state: dict[str, Any],
    *,
    claim: AtomicClaimAudit | None = None,
    issues: list[ReportEvaluationIssue] | None = None,
    scores: PostSynthesisScores | None = None,
) -> PostSynthesisEvaluationRun:
    return PostSynthesisEvaluationRun(
        status="completed",
        evaluation=_evaluation(
            state,
            audits=_controller_segment_audits(
                state,
                first_claim=claim or _supported_claim(state),
            ),
            issues=issues,
            scores=scores,
        ),
        error_code=None,
        **_report_subject(state),
        evaluator_model="test-post-evaluator",
        attempts=1,
        duration_ms=4,
    )


def _decision_for(
    state: dict[str, Any],
    *,
    claim: AtomicClaimAudit | None = None,
    issues: list[ReportEvaluationIssue] | None = None,
    scores: PostSynthesisScores | None = None,
):
    state = deepcopy(state)
    state["post_synthesis_evaluation_run"] = _completed_post_run(
        state,
        claim=claim,
        issues=issues,
        scores=scores,
    )
    return post_controller_module.decide_post_synthesis_route(state)


def test_exact_post_accept_thresholds_pass() -> None:
    state = _state()
    scores = _scores(
        intent_alignment=80,
        material_claim_grounding=90,
        citation_fidelity=90,
        citation_completeness=85,
        contradiction_handling=80,
        coverage=80,
    )

    decision = _decision_for(state, scores=scores)

    assert post_controller_module.POST_ACCEPT_OVERALL_SCORE == 85.0
    assert decision.route == "accept"
    assert decision.repair_stage.value == "initial"


@pytest.mark.parametrize(
    ("score_name", "below_threshold"),
    [
        ("intent_alignment", 79),
        ("material_claim_grounding", 89),
        ("citation_fidelity", 89),
        ("citation_completeness", 84),
        ("contradiction_handling", 79),
        ("coverage", 79),
    ],
)
def test_any_critical_post_score_below_threshold_blocks_accept(
    score_name: str,
    below_threshold: int,
) -> None:
    state = _state()
    scores = _scores(**{score_name: below_threshold})

    decision = _decision_for(state, scores=scores)

    assert decision.route != "accept"


def test_overstatement_routes_to_synthesis_only_targeted_repair() -> None:
    state = _state()
    claim = _supported_claim(state).model_copy(update={"calibration": "overstated"})
    issue = _issue(
        state,
        issue_id="issue-overstatement",
        category="overstatement",
        stage="synthesis",
        target_ids=[],
    )

    decision = _decision_for(state, claim=claim, issues=[issue])

    assert decision.route == "targeted_repair"
    assert decision.repair_stage.value == "synthesis"
    assert decision.target_report_segment_ids == issue.segment_ids
    assert decision.affected_sub_question_ids == []


def test_local_evidence_gap_routes_exact_subquestion_to_evidence_repair() -> None:
    state = _state()
    claim = _unsupported_claim(state)
    issue = _issue(
        state,
        issue_id="issue-local-evidence",
        category="unsupported_claim",
        stage="evidence",
        target_ids=["sq-a"],
    ).model_copy(update={"claim_ids": [claim.claim_id]})

    decision = _decision_for(state, claim=claim, issues=[issue])

    assert decision.route == "targeted_repair"
    assert decision.repair_stage.value == "evidence"
    assert decision.affected_sub_question_ids == ["sq-a"]
    assert decision.target_report_segment_ids == issue.segment_ids


@pytest.mark.parametrize(
    ("issue_category", "targets", "expected_route", "expected_stage"),
    [
        ("coverage_gap", ["sq-a"], "partial_replan", "partial_replan"),
        ("contract_violation", ["sq-a", "sq-b"], "full_replan", "full_replan"),
    ],
)
def test_plan_defects_use_partial_or_full_existing_routes(
    issue_category: str,
    targets: list[str],
    expected_route: str,
    expected_stage: str,
) -> None:
    state = _state()
    issue = _issue(
        state,
        issue_id=f"issue-{issue_category}",
        category=issue_category,
        stage="plan",
        target_ids=targets,
        severity="blocker" if expected_route == "full_replan" else "major",
    )

    decision = _decision_for(state, issues=[issue])

    assert decision.route == expected_route
    assert decision.repair_stage.value == expected_stage


def test_mixed_issue_precedence_is_plan_then_evidence_then_synthesis() -> None:
    state = _state()
    synthesis_issue = _issue(
        state,
        issue_id="issue-writing",
        category="overstatement",
        stage="synthesis",
        target_ids=[],
    )
    evidence_issue = _issue(
        state,
        issue_id="issue-evidence",
        category="unsupported_claim",
        stage="evidence",
        target_ids=["sq-a"],
    )
    plan_issue = _issue(
        state,
        issue_id="issue-plan",
        category="coverage_gap",
        stage="plan",
        target_ids=["sq-b"],
    )

    evidence_over_synthesis = _decision_for(
        state,
        issues=[synthesis_issue, evidence_issue],
    )
    plan_over_evidence = _decision_for(
        state,
        issues=[synthesis_issue, evidence_issue, plan_issue],
    )

    assert evidence_over_synthesis.route == "targeted_repair"
    assert evidence_over_synthesis.repair_stage.value == "evidence"
    assert plan_over_evidence.route in {"partial_replan", "full_replan"}
    assert plan_over_evidence.repair_stage.value in {"partial_replan", "full_replan"}


def test_authoritative_post_decisions_still_have_exactly_five_public_routes() -> None:
    state = _state()
    accept = _decision_for(state)
    synthesis = _decision_for(
        state,
        issues=[
            _issue(
                state,
                issue_id="i-synthesis",
                category="overstatement",
                stage="synthesis",
                target_ids=[],
            )
        ],
    )
    evidence = _decision_for(
        state,
        claim=_unsupported_claim(state),
        issues=[
            _issue(
                state,
                issue_id="i-evidence",
                category="unsupported_claim",
                stage="evidence",
                target_ids=["sq-a"],
            ).model_copy(update={"claim_ids": ["claim-material"]})
        ],
    )
    partial = _decision_for(
        state,
        issues=[
            _issue(
                state,
                issue_id="i-partial",
                category="coverage_gap",
                stage="plan",
                target_ids=["sq-a"],
            )
        ],
    )
    full = _decision_for(
        state,
        issues=[
            _issue(
                state,
                issue_id="i-full",
                category="contract_violation",
                stage="plan",
                target_ids=["sq-a", "sq-b"],
                severity="blocker",
            )
        ],
    )
    failed = PostSynthesisEvaluationRun(
        status="failed",
        evaluation=None,
        error_code="provider_error",
        evaluator_model="test-post-evaluator",
        attempts=2,
        duration_ms=4,
    )
    stopped = post_controller_module.decide_post_synthesis_route(
        {**state, "post_synthesis_evaluation_run": failed}
    )

    observed_routes = {
        accept.route,
        synthesis.route,
        evidence.route,
        partial.route,
        full.route,
        stopped.route,
    }
    assert observed_routes == {
        "accept",
        "targeted_repair",
        "partial_replan",
        "full_replan",
        "stop_incomplete",
    }
    assert synthesis.repair_stage.value == "synthesis"
    assert evidence.repair_stage.value == "evidence"


def test_repeated_post_fingerprint_escalates_and_cannot_loop_same_action() -> None:
    state = _state()
    issue = _issue(
        state,
        issue_id="issue-repeat",
        category="unsupported_claim",
        stage="evidence",
        target_ids=["sq-a"],
    ).model_copy(update={"claim_ids": ["claim-material"]})
    state["post_synthesis_evaluation_run"] = _completed_post_run(
        state,
        claim=_unsupported_claim(state),
        issues=[issue],
    )

    first_update = post_controller_module.post_synthesis_controller_node(state)
    first = first_update["post_synthesis_controller_decision"]
    second_state = {**state, **first_update}
    second_update = post_controller_module.post_synthesis_controller_node(second_state)
    second = second_update["post_synthesis_controller_decision"]

    assert first.route == "targeted_repair"
    assert first.repair_stage.value == "evidence"
    assert second.route in {"partial_replan", "full_replan", "stop_incomplete"}
    assert (second.route, second.repair_stage) != (first.route, first.repair_stage)
    assert len(second_update["post_recovery_fingerprints"]) >= 1


@pytest.mark.parametrize(
    "budget_values",
    [
        {"post_evaluations_used": 4, "post_evaluation_limit": 4},
        {"synthesis_repairs_used": 2, "synthesis_repair_limit": 2},
    ],
)
def test_post_evaluation_and_synthesis_revision_budgets_stop_finitely(
    budget_values: dict[str, int],
) -> None:
    state = _state()
    state["budget_snapshot"] = BudgetSnapshot(**budget_values)
    issue = _issue(
        state,
        issue_id="issue-budget",
        category="overstatement",
        stage="synthesis",
        target_ids=[],
    )

    decision = _decision_for(state, issues=[issue])

    assert decision.route == "stop_incomplete"


def test_claim_free_audits_for_material_surfaces_cannot_accept() -> None:
    state = _state()
    state["post_synthesis_evaluation_run"] = PostSynthesisEvaluationRun(
        status="completed",
        evaluation=_evaluation(state),
        error_code=None,
        **_report_subject(state),
        evaluator_model="test-post-evaluator",
        attempts=1,
        duration_ms=4,
    )

    decision = post_controller_module.decide_post_synthesis_route(state)

    assert decision.route != "accept"
    assert decision.reason_code in {
        "material_claim_audit_missing",
        "invalid_or_failed_post_evaluation",
    }


def test_one_token_claim_audit_cannot_hide_unaudited_key_findings() -> None:
    state = _state()
    state["post_synthesis_evaluation_run"] = PostSynthesisEvaluationRun(
        status="completed",
        evaluation=_evaluation(
            state,
            audits=_segment_audits(state, claim=_supported_claim(state)),
        ),
        error_code=None,
        **_report_subject(state),
        evaluator_model="test-post-evaluator",
        attempts=1,
        duration_ms=4,
    )

    decision = post_controller_module.decide_post_synthesis_route(state)

    assert decision.route != "accept"
    assert decision.reason_code in {
        "material_claim_audit_missing",
        "invalid_or_failed_post_evaluation",
    }


@pytest.mark.parametrize(
    "bad_update",
    [
        {"post_synthesis_evaluation_run": {"status": "completed"}},
        {"budget_snapshot": {"post_evaluation_limit": "not-an-integer"}},
        {"post_routing_history": [{"route": "not-a-route"}]},
    ],
)
def test_invalid_dict_shaped_post_state_fails_closed_without_raising(
    bad_update: dict[str, Any],
) -> None:
    state = {**_state(), **bad_update}

    decision = post_controller_module.decide_post_synthesis_route(state)

    assert decision.route == "stop_incomplete"


def test_valid_checkpoint_shaped_state_revalidates_and_accepts() -> None:
    state = _state()
    state["post_synthesis_evaluation_run"] = _completed_post_run(state)
    checkpoint_state = {
        **state,
        "sub_questions": [question.model_dump(mode="json") for question in state["sub_questions"]],
        "sub_reports": [report.model_dump(mode="json") for report in state["sub_reports"]],
        "candidate_report": state["candidate_report"].model_dump(mode="json"),
        "post_synthesis_evaluation_run": state[
            "post_synthesis_evaluation_run"
        ].model_dump(mode="json"),
        "budget_snapshot": state["budget_snapshot"].model_dump(mode="json"),
    }

    decision = post_controller_module.decide_post_synthesis_route(checkpoint_state)

    assert decision.route == "accept"


def test_checkpoint_claim_not_anchored_to_segment_fails_closed() -> None:
    state = _state()
    run = _completed_post_run(state).model_dump(mode="json")
    executive_audit = next(
        audit
        for audit in run["evaluation"]["segment_audits"]
        if audit["segment_id"] == "seg-executive-summary"
    )
    executive_audit["claims"][0]["claim_text"] = "Invented wording outside the draft."
    state.update(
        {
            "sub_questions": [item.model_dump(mode="json") for item in state["sub_questions"]],
            "sub_reports": [item.model_dump(mode="json") for item in state["sub_reports"]],
            "candidate_report": state["candidate_report"].model_dump(mode="json"),
            "post_synthesis_evaluation_run": run,
        }
    )

    decision = post_controller_module.decide_post_synthesis_route(state)

    assert decision.route == "stop_incomplete"
    assert decision.reason_code == "invalid_or_failed_post_evaluation"


def test_derived_only_checkpoint_evidence_cannot_publish() -> None:
    state = _state()
    state["post_synthesis_evaluation_run"] = _completed_post_run(state).model_dump(
        mode="json"
    )
    reports = [report.model_dump(mode="json") for report in state["sub_reports"]]
    for report in reports:
        for source in report["sources"]:
            source["excerpt"] = ""
    state["sub_reports"] = reports
    state["sub_questions"] = [
        question.model_dump(mode="json") for question in state["sub_questions"]
    ]
    state["candidate_report"] = state["candidate_report"].model_dump(mode="json")

    decision = post_controller_module.decide_post_synthesis_route(state)

    assert decision.route == "stop_incomplete"
    assert decision.reason_code == "invalid_or_failed_post_evaluation"


def test_missing_or_malformed_post_decision_dispatches_fail_closed() -> None:
    assert post_controller_module.route_after_post_controller({}) == "stop_incomplete"
    assert post_controller_module.route_after_post_controller(
        {"post_synthesis_controller_decision": {"route": "not-a-route"}}
    ) == "stop_incomplete"


def test_alternating_post_repair_diagnoses_cannot_oscillate_forever() -> None:
    state = _state()
    synthesis_issue = _issue(
        state,
        issue_id="issue-cycle-writing",
        category="overstatement",
        stage="synthesis",
        target_ids=[],
    )
    evidence_issue = _issue(
        state,
        issue_id="issue-cycle-evidence",
        category="unsupported_claim",
        stage="evidence",
        target_ids=["sq-a"],
    ).model_copy(update={"claim_ids": ["claim-material"]})

    state["post_synthesis_evaluation_run"] = _completed_post_run(
        state,
        issues=[synthesis_issue],
    )
    first = post_controller_module.post_synthesis_controller_node(state)
    state = {**state, **first}
    state["post_synthesis_evaluation_run"] = _completed_post_run(
        state,
        claim=_unsupported_claim(state),
        issues=[evidence_issue],
    )
    second = post_controller_module.post_synthesis_controller_node(state)
    state = {**state, **second}
    state["post_synthesis_evaluation_run"] = _completed_post_run(
        state,
        issues=[synthesis_issue],
    )
    third = post_controller_module.post_synthesis_controller_node(state)
    third_decision = third["post_synthesis_controller_decision"]

    assert len(third["post_routing_history"]) == 3
    assert third_decision.route in {
        "partial_replan",
        "full_replan",
        "stop_incomplete",
    }
    assert not (
        third_decision.route == "targeted_repair"
        and third_decision.repair_stage.value == "synthesis"
    )


@pytest.mark.parametrize("support", ["unsupported", "contradicted", "unverifiable"])
def test_llm_high_scores_cannot_override_unsupported_material_claim(support: str) -> None:
    state = _state()
    claim = _unsupported_claim(state, support=support)
    evaluation = _evaluation(
        state,
        audits=_controller_segment_audits(state, first_claim=claim),
    )
    state["post_synthesis_evaluation_run"] = PostSynthesisEvaluationRun(
        status="completed",
        evaluation=evaluation,
        error_code=None,
        **_report_subject(state),
        evaluator_model="test-evaluator",
        attempts=1,
        duration_ms=1,
    )

    decision = post_controller_module.decide_post_synthesis_route(state)

    assert decision.route != "accept"
    assert decision.repair_stage.value in {"evidence", "plan", "initial"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("unknown_kind", "expected_code"),
    [
        ("evidence", "invalid_references"),
        ("source", "invalid_references"),
        ("subquestion", "invalid_references"),
    ],
)
async def test_unknown_evidence_source_or_subquestion_reference_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    unknown_kind: str,
    expected_code: str,
) -> None:
    state = _state()
    retained_sources, retained_evidence = build_evidence_inventory(state["sub_reports"])
    valid_source_id = retained_sources[0].source_id
    valid_evidence = retained_evidence[0]
    claim = AtomicClaimAudit(
        claim_id="claim-reference-check",
        claim_text="A claim with audited provenance.",
        materiality="critical",
        support="supported",
        evidence_refs=[
            ClaimEvidenceReference(
                evidence_id=(
                    "ev-unknown"
                    if unknown_kind == "evidence"
                    else valid_evidence.evidence_id
                ),
                supporting_excerpt=valid_evidence.text,
            )
        ],
        citation=ClaimCitationAudit(
            status="correct",
            cited_source_ids=[
                "src-unknown" if unknown_kind == "source" else valid_source_id
            ],
            rationale="Audited citation.",
        ),
        calibration="accurate",
        rationale="Reference validation fixture.",
    )
    issue = ReportEvaluationIssue(
        id="issue-reference-check",
        category="unsupported_claim",
        severity="major",
        claim_ids=[claim.claim_id],
        segment_ids=[_segments(state)[0].id],
        affected_sub_question_ids=[
            "sq-unknown" if unknown_kind == "subquestion" else "sq-a"
        ],
        suggested_repair_stage="evidence",
        description="Reference validation fixture.",
        acceptance_criteria=["All references resolve to retained evidence."],
    )
    fake = _FakeStructuredLLM(
        response=_evaluation(
            state,
            audits=_segment_audits(state, claim=claim),
            issues=[issue],
        )
    )
    _patch_llm(monkeypatch, fake)

    update = await evaluate_report_module.post_synthesis_evaluate_node(state)

    run = update["post_synthesis_evaluation_run"]
    assert run.status == "failed"
    assert run.evaluation is None
    assert run.error_code == expected_code


@pytest.mark.asyncio
async def test_known_but_unrelated_source_cannot_validate_evidence_citation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    retained_sources, retained_evidence = build_evidence_inventory(state["sub_reports"])
    unit = retained_evidence[0]
    unrelated_source_id = next(
        source.source_id
        for source in retained_sources
        if source.source_id not in unit.source_ids
    )
    claim = AtomicClaimAudit(
        claim_id="claim-source-binding",
        claim_text="A claim whose citation must bind to its evidence unit.",
        materiality="critical",
        support="supported",
        evidence_refs=[
            ClaimEvidenceReference(
                evidence_id=unit.evidence_id,
                supporting_excerpt=unit.text,
            )
        ],
        citation=ClaimCitationAudit(
            status="correct",
            cited_source_ids=[unrelated_source_id],
            rationale="This globally known source is unrelated to the referenced unit.",
        ),
        calibration="accurate",
        rationale="Citation-to-evidence binding fixture.",
    )
    fake = _FakeStructuredLLM(
        response=_evaluation(
            state,
            audits=_segment_audits(state, claim=claim),
        )
    )
    _patch_llm(monkeypatch, fake)

    update = await evaluate_report_module.post_synthesis_evaluate_node(state)

    run = update["post_synthesis_evaluation_run"]
    assert run.status == "failed"
    assert run.evaluation is None
    assert run.error_code == "invalid_references"
