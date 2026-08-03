"""Focused contracts for post-synthesis evidence and plan repair context."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.deep_research.models import (
    AtomicClaimAudit,
    BudgetSnapshot,
    ClaimCitationAudit,
    ClaimEvidenceReference,
    EvidenceIssue,
    EvidenceRepairDirective,
    PostSynthesisEvaluation,
    PostSynthesisEvaluationRun,
    PostSynthesisRoutingDecision,
    PostSynthesisScores,
    PreSynthesisEvaluation,
    PreSynthesisEvaluationRun,
    PreSynthesisScores,
    RepairPlan,
    RepairStage,
    ReportEvaluationIssue,
    ReportSection,
    ReportSegmentAudit,
    ResearchReport,
    SourceRef,
    SubQuestion,
)
from app.deep_research.nodes import replan as replan_module
from app.deep_research.provenance import build_report_segments, report_digest


POST_SCORES = {
    "intent_alignment": 61,
    "material_claim_grounding": 62,
    "citation_fidelity": 63,
    "citation_completeness": 64,
    "contradiction_handling": 65,
    "coverage": 66,
    "coherence": 67,
    "limitations_calibration": 68,
}


def _question(question_id: str) -> SubQuestion:
    return SubQuestion(
        id=question_id,
        question=f"Bounded question for {question_id}",
        search_queries=[f"original query {question_id}"],
        priority=1,
        rationale="Required by the current research contract.",
    )


def _candidate_report() -> ResearchReport:
    return ResearchReport(
        title="Candidate title safe marker",
        executive_summary="CANDIDATE-EXECUTIVE-PROSE-MUST-NOT-ENTER-REPLAN",
        sections=[
            ReportSection(
                heading="Candidate section",
                content="CANDIDATE-SECTION-PROSE-MUST-NOT-ENTER-REPLAN",
            )
        ],
        key_findings=["CANDIDATE-KEY-FINDING-MUST-NOT-ENTER-REPLAN"],
        limitations="CANDIDATE-LIMITATIONS-MUST-NOT-ENTER-REPLAN",
        sources=[
            SourceRef(
                source_id="src-current",
                title="Sensitive source title",
                url="https://raw-source.example/private?secret=source-secret",
            )
        ],
    )


def _pre_evaluation_with_stale_directive() -> PreSynthesisEvaluationRun:
    issue = EvidenceIssue(
        id="pre-stale-issue",
        category="source_quality",
        severity="minor",
        description="PRE-STALE-DIAGNOSTIC-MUST-NOT-ENTER-POST-REPAIR",
        affected_sub_question_ids=["sq-a"],
        source_urls=["https://pre-stale.example/raw"],
    )
    directive = EvidenceRepairDirective(
        id="pre-stale-directive",
        issue_ids=[issue.id],
        target_sub_question_ids=["sq-a"],
        objective="PRE-STALE-OBJECTIVE-MUST-NOT-ENTER-POST-REPAIR",
        suggested_queries=["PRE-STALE-QUERY-MUST-NOT-BE-USED"],
        acceptance_criteria=["PRE-STALE-ACCEPTANCE-MUST-NOT-ENTER-POST-REPAIR"],
    )
    return PreSynthesisEvaluationRun(
        status="completed",
        evaluation=PreSynthesisEvaluation(
            schema_version="pre-synthesis-evaluation.v1",
            rubric_version="pre-synthesis-rubric.v1",
            assessed_sub_question_ids=["sq-a", "sq-b"],
            scores=PreSynthesisScores(
                intent_alignment=99,
                must_answer_coverage=99,
                source_relevance=99,
                source_quality=99,
                source_diversity=99,
                source_recency=99,
                grounding_consistency=99,
                contradiction_handling=99,
                synthesis_readiness=99,
            ),
            issues=[issue],
            repair_directives=[directive],
            unresolved_questions=[],
            evaluation_limitations=[],
            summary="The earlier evidence gate otherwise passed.",
        ),
        error_code=None,
        evaluator_model="stale-pre-evaluator",
        attempts=1,
        duration_ms=1,
    )


def _post_issue(
    report: ResearchReport,
    *,
    issue_id: str,
    category: str,
    stage: str,
    affected_ids: list[str],
    suggested_queries: list[str] | None = None,
) -> tuple[ReportEvaluationIssue, str, str]:
    target_segment_id = next(
        segment.id
        for segment in build_report_segments(report)
        if segment.component == "section"
    )
    claim_id = "claim-current-post-evaluation"
    issue = ReportEvaluationIssue(
        id=issue_id,
        category=category,
        severity="blocker" if category == "contract_violation" else "major",
        claim_ids=[claim_id],
        segment_ids=[target_segment_id],
        affected_sub_question_ids=affected_ids,
        suggested_repair_stage=stage,
        description="SANITIZED-POST-ISSUE-DESCRIPTION",
        acceptance_criteria=["SANITIZED-POST-ACCEPTANCE-CRITERION"],
    )
    if suggested_queries is not None:
        # The v1 evaluator contract does not yet declare this optional hint, while
        # deployed evaluator objects can carry it. Keep this fixture compatible
        # with both forms without weakening the strict persisted model contract.
        object.__setattr__(issue, "suggested_queries", list(suggested_queries))
        assert getattr(issue, "suggested_queries", None) == suggested_queries
    return issue, target_segment_id, claim_id


def _post_run(
    report: ResearchReport,
    issue: ReportEvaluationIssue,
    *,
    claim_id: str,
    report_version: int = 7,
) -> PostSynthesisEvaluationRun:
    target_segment_id = issue.segment_ids[0]
    audits = [
        ReportSegmentAudit(
            segment_id=segment.id,
            contains_material_claims=segment.id == target_segment_id,
            claims=(
                [
                    AtomicClaimAudit(
                        claim_id=claim_id,
                        claim_text="A material claim requiring repair.",
                        materiality="critical",
                        support="unsupported",
                        evidence_refs=[
                            ClaimEvidenceReference(
                                evidence_id="ev-current",
                                supporting_excerpt="A retained excerpt.",
                            )
                        ],
                        citation=ClaimCitationAudit(
                            status="missing",
                            cited_source_ids=[],
                            rationale="The material claim needs a citation.",
                        ),
                        calibration="overstated",
                        rationale="The retained evidence does not support the wording.",
                    )
                ]
                if segment.id == target_segment_id
                else []
            ),
        )
        for segment in build_report_segments(report)
    ]
    return PostSynthesisEvaluationRun(
        status="completed",
        evaluation=PostSynthesisEvaluation(
            schema_version="post-synthesis-eval.v1",
            rubric_version="report-quality.v1",
            segment_audits=audits,
            scores=PostSynthesisScores(**POST_SCORES),
            issues=[issue],
            unresolved_questions=[],
            summary="The current candidate requires a bounded repair.",
        ),
        error_code=None,
        report_digest=report_digest(report),
        report_version=report_version,
        evaluator_model="current-post-evaluator",
        attempts=1,
        duration_ms=7,
    )


def _decision(
    *,
    route: str,
    stage: RepairStage,
    issue_id: str,
    affected_ids: list[str],
    target_segment_id: str,
    report: ResearchReport,
    report_version: int = 7,
) -> PostSynthesisRoutingDecision:
    return PostSynthesisRoutingDecision(
        route=route,
        repair_stage=stage,
        reason_code="current_post_repair",
        reason="The current post-synthesis evaluation selected this repair.",
        affected_sub_question_ids=affected_ids,
        target_report_segment_ids=[target_segment_id],
        issue_ids=[issue_id],
        major_issue_ids=[issue_id],
        weighted_overall_score=64.5,
        affected_priority_ratio=0.5,
        score_gain=None,
        closed_major_issue_ids=[],
        fingerprint="a" * 64,
        escalated_from=None,
        budget=BudgetSnapshot(),
        report_digest=report_digest(report),
        report_version=report_version,
    )


def _state_for(
    *,
    route: str,
    stage: RepairStage,
    category: str,
    affected_ids: list[str],
    suggested_queries: list[str] | None = None,
) -> dict[str, Any]:
    report = _candidate_report()
    issue, target_segment_id, claim_id = _post_issue(
        report,
        issue_id="post-current-issue",
        category=category,
        stage="evidence" if stage == RepairStage.EVIDENCE else "plan",
        affected_ids=affected_ids,
        suggested_queries=suggested_queries,
    )
    decision = _decision(
        route=route,
        stage=stage,
        issue_id=issue.id,
        affected_ids=affected_ids,
        target_segment_id=target_segment_id,
        report=report,
    )
    return {
        "topic": "Safe bounded research topic",
        "api_key": "sk-api-key-must-never-enter-repair-context",
        "llm_base_url": "https://private-llm.example/v1?token=base-url-secret",
        "llm_model": "test-model",
        "sub_questions": [_question("sq-a"), _question("sq-b")],
        "sub_reports": [],
        "failed_queries": [],
        "pre_synthesis_evaluation_run": _pre_evaluation_with_stale_directive(),
        "post_synthesis_evaluation_run": _post_run(
            report,
            issue,
            claim_id=claim_id,
        ),
        "candidate_report": report,
        "report_version": 7,
        "plan_version": 3,
        "controller_decision": decision,
        "post_synthesis_controller_decision": decision,
    }


@pytest.mark.asyncio
async def test_post_evidence_repair_uses_current_queries_not_stale_pre() -> None:
    state = _state_for(
        route="targeted_repair",
        stage=RepairStage.EVIDENCE,
        category="unsupported_claim",
        affected_ids=["sq-a"],
        suggested_queries=[
            "current post primary query",
            "current post corroborating query",
        ],
    )

    update = replan_module.prepare_targeted_repair_node(state)
    updated_questions = {question.id: question for question in update["sub_questions"]}

    assert update["repair_preparation_status"] == "ready"
    assert update["execution_target_ids"] == ["sq-a"]
    assert updated_questions["sq-a"].search_queries == [
        "current post primary query",
        "current post corroborating query",
        "original query sq-a",
    ]
    assert "PRE-STALE-QUERY-MUST-NOT-BE-USED" not in updated_questions[
        "sq-a"
    ].search_queries
    assert updated_questions["sq-b"].search_queries == ["original query sq-b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("post_run_case", ["missing", "failed", "mismatched"])
@pytest.mark.parametrize(
    ("node_name", "route", "stage", "category"),
    [
        (
            "prepare_targeted_repair_node",
            "targeted_repair",
            RepairStage.EVIDENCE,
            "unsupported_claim",
        ),
        (
            "partial_replan_node",
            "partial_replan",
            RepairStage.PARTIAL_REPLAN,
            "coverage_gap",
        ),
        (
            "full_replan_node",
            "full_replan",
            RepairStage.FULL_REPLAN,
            "contract_violation",
        ),
    ],
)
async def test_invalid_current_post_evaluation_fails_closed_before_llm(
    monkeypatch: pytest.MonkeyPatch,
    node_name: str,
    route: str,
    stage: RepairStage,
    category: str,
    post_run_case: str,
) -> None:
    affected_ids = ["sq-a", "sq-b"] if route == "full_replan" else ["sq-a"]
    state = _state_for(
        route=route,
        stage=stage,
        category=category,
        affected_ids=affected_ids,
    )
    if post_run_case == "missing":
        state.pop("post_synthesis_evaluation_run")
    elif post_run_case == "failed":
        state["post_synthesis_evaluation_run"] = PostSynthesisEvaluationRun(
            status="failed",
            evaluation=None,
            error_code="provider_error",
            evaluator_model="current-post-evaluator",
            attempts=2,
            duration_ms=7,
        )
    else:
        decision = state["post_synthesis_controller_decision"]
        mismatched = decision.model_copy(
            update={
                "issue_ids": ["post-different-issue"],
                "major_issue_ids": ["post-different-issue"],
            }
        )
        state["controller_decision"] = mismatched
        state["post_synthesis_controller_decision"] = mismatched

    calls: list[str] = []

    def forbidden_factory(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("called")
        raise AssertionError("invalid post repair context must not invoke the LLM")

    monkeypatch.setattr(replan_module, "make_structured_llm", forbidden_factory)
    update = getattr(replan_module, node_name)(state)
    if hasattr(update, "__await__"):
        update = await update

    assert calls == []
    assert update["repair_preparation_status"] == "failed"
    assert update["terminal_status"] == "incomplete"
    assert update["workflow_error_code"]
    assert update["execution_target_ids"] is None


def _checkpoint_dict_state(state: dict[str, Any]) -> dict[str, Any]:
    checkpoint = dict(state)
    for key in (
        "candidate_report",
        "controller_decision",
        "post_synthesis_controller_decision",
        "post_synthesis_evaluation_run",
    ):
        checkpoint[key] = state[key].model_dump(mode="json")
    checkpoint["sub_questions"] = [
        question.model_dump(mode="json") for question in state["sub_questions"]
    ]
    return checkpoint


@pytest.mark.asyncio
@pytest.mark.parametrize("state_shape", ["typed", "checkpoint_dict"])
@pytest.mark.parametrize("stale_subject", ["candidate_digest", "report_version"])
@pytest.mark.parametrize(
    ("node_name", "route", "stage", "category", "affected_ids"),
    [
        (
            "prepare_targeted_repair_node",
            "targeted_repair",
            RepairStage.EVIDENCE,
            "unsupported_claim",
            ["sq-a"],
        ),
        (
            "partial_replan_node",
            "partial_replan",
            RepairStage.PARTIAL_REPLAN,
            "coverage_gap",
            ["sq-a"],
        ),
        (
            "full_replan_node",
            "full_replan",
            RepairStage.FULL_REPLAN,
            "contract_violation",
            ["sq-a", "sq-b"],
        ),
    ],
)
async def test_stale_post_report_subject_fails_closed_before_llm(
    monkeypatch: pytest.MonkeyPatch,
    node_name: str,
    route: str,
    stage: RepairStage,
    category: str,
    affected_ids: list[str],
    stale_subject: str,
    state_shape: str,
) -> None:
    state = _state_for(
        route=route,
        stage=stage,
        category=category,
        affected_ids=affected_ids,
        suggested_queries=["stale post query must never execute"],
    )
    if stale_subject == "candidate_digest":
        state["candidate_report"] = state["candidate_report"].model_copy(
            update={"executive_summary": "A different candidate report body."}
        )
    else:
        state["report_version"] += 1
    if state_shape == "checkpoint_dict":
        state = _checkpoint_dict_state(state)

    calls: list[str] = []

    def forbidden_factory(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("called")
        raise AssertionError("stale post report subjects must not invoke the LLM")

    monkeypatch.setattr(replan_module, "make_structured_llm", forbidden_factory)
    update = getattr(replan_module, node_name)(state)
    if hasattr(update, "__await__"):
        update = await update

    assert calls == []
    assert update["repair_preparation_status"] == "failed"
    assert update["terminal_status"] == "incomplete"
    assert update["workflow_error_code"] == "invalid_post_repair_context"
    assert update["execution_target_ids"] is None


@pytest.mark.parametrize("state_shape", ["typed", "checkpoint_dict"])
@pytest.mark.parametrize(
    ("artifact_key", "stale_field", "stale_value"),
    [
        ("post_synthesis_evaluation_run", "report_digest", "b" * 64),
        ("post_synthesis_evaluation_run", "report_version", 6),
        ("controller_decision", "report_digest", "c" * 64),
        ("controller_decision", "report_version", 6),
    ],
)
def test_each_post_subject_credential_must_match_current_candidate(
    artifact_key: str,
    stale_field: str,
    stale_value: str | int,
    state_shape: str,
) -> None:
    state = _state_for(
        route="targeted_repair",
        stage=RepairStage.EVIDENCE,
        category="unsupported_claim",
        affected_ids=["sq-a"],
    )
    if state_shape == "checkpoint_dict":
        state = _checkpoint_dict_state(state)
        state[artifact_key][stale_field] = stale_value
    else:
        state[artifact_key] = state[artifact_key].model_copy(
            update={stale_field: stale_value}
        )

    update = replan_module.prepare_targeted_repair_node(state)

    assert update["repair_preparation_status"] == "failed"
    assert update["workflow_error_code"] == "invalid_post_repair_context"
    assert update["execution_target_ids"] is None


class _CapturingRepairPlanner:
    def __init__(self, response: RepairPlan):
        self.response = response
        self.messages: list[Any] = []

    async def ainvoke(self, messages: Any) -> RepairPlan:
        self.messages.append(messages)
        return self.response


def _find_mapping_with_keys(
    value: Any,
    required_keys: set[str],
) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if required_keys <= set(value):
            return value
        for nested in value.values():
            match = _find_mapping_with_keys(nested, required_keys)
            if match is not None:
                return match
    elif isinstance(value, list):
        for nested in value:
            match = _find_mapping_with_keys(nested, required_keys)
            if match is not None:
                return match
    return None


def _repair_payload(planner: _CapturingRepairPlanner) -> dict[str, Any]:
    assert len(planner.messages) == 1
    user_content = planner.messages[0][1]["content"]
    marker = "REPAIR_CONTEXT_JSON:\n"
    assert marker in user_content
    return json.loads(user_content.split(marker, 1)[1])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node_name", "route", "stage", "category", "affected_ids"),
    [
        (
            "partial_replan_node",
            "partial_replan",
            RepairStage.PARTIAL_REPLAN,
            "coverage_gap",
            ["sq-a"],
        ),
        (
            "full_replan_node",
            "full_replan",
            RepairStage.FULL_REPLAN,
            "contract_violation",
            ["sq-a", "sq-b"],
        ),
    ],
)
async def test_post_plan_repair_context_is_current_minimal_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    node_name: str,
    route: str,
    stage: RepairStage,
    category: str,
    affected_ids: list[str],
) -> None:
    state = _state_for(
        route=route,
        stage=stage,
        category=category,
        affected_ids=affected_ids,
    )
    replacement_id = "sq-new-full" if route == "full_replan" else "sq-new-a"
    planner = _CapturingRepairPlanner(
        RepairPlan(
            sub_questions=[_question(replacement_id)],
            overall_approach="Repair only the evaluator-selected plan scope.",
        )
    )
    monkeypatch.setattr(
        replan_module,
        "make_structured_llm",
        lambda *_args, **_kwargs: planner,
    )

    update = await getattr(replan_module, node_name)(state)

    assert update["repair_preparation_status"] == "ready"
    payload = _repair_payload(planner)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    issue = state["post_synthesis_evaluation_run"].evaluation.issues[0]
    decision = state["post_synthesis_controller_decision"]

    assert category in serialized
    assert issue.description in serialized
    assert issue.segment_ids[0] in serialized
    assert issue.claim_ids[0] in serialized
    assert all(identifier in serialized for identifier in affected_ids)
    assert issue.acceptance_criteria[0] in serialized

    score_payload = _find_mapping_with_keys(payload, set(POST_SCORES))
    assert score_payload is not None
    assert {key: score_payload[key] for key in POST_SCORES} == POST_SCORES
    assert state["candidate_report"].title in serialized
    assert state["report_version"] in {
        value
        for value in _flatten_scalar_values(payload)
        if isinstance(value, int)
    }
    assert all(
        segment_id in serialized
        for segment_id in decision.target_report_segment_ids
    )

    forbidden_markers = [
        state["api_key"],
        state["llm_base_url"],
        state["candidate_report"].executive_summary,
        state["candidate_report"].sections[0].content,
        state["candidate_report"].key_findings[0],
        state["candidate_report"].limitations,
        "source-secret",
        "PRE-STALE-DIAGNOSTIC-MUST-NOT-ENTER-POST-REPAIR",
        "PRE-STALE-OBJECTIVE-MUST-NOT-ENTER-POST-REPAIR",
        "PRE-STALE-QUERY-MUST-NOT-BE-USED",
        "PRE-STALE-ACCEPTANCE-MUST-NOT-ENTER-POST-REPAIR",
    ]
    assert all(marker not in serialized for marker in forbidden_markers)
    assert "https://" not in serialized


def _flatten_scalar_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [
            item
            for nested in value.values()
            for item in _flatten_scalar_values(nested)
        ]
    if isinstance(value, list):
        return [item for nested in value for item in _flatten_scalar_values(nested)]
    return [value]
