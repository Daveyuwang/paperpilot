from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

import structlog
from pydantic import ValidationError

from app.deep_research.models import (
    BudgetSnapshot,
    PostSynthesisEvaluationRun,
    PostSynthesisRoute,
    PostSynthesisRoutingDecision,
    RepairStage,
    ResearchReport,
    SubQuestion,
    SubReport,
)
from app.deep_research.nodes.evaluate_report import (
    validate_post_synthesis_reference_contract,
)
from app.deep_research.provenance import (
    build_evidence_inventory,
    build_report_segments,
    evaluation_digest,
    report_digest,
)
from app.deep_research.state import DeepResearchState

logger = structlog.get_logger()

POST_ACCEPT_OVERALL_SCORE = 85.0
POST_MIN_GROUNDING_SCORE = 90
POST_MIN_CITATION_FIDELITY_SCORE = 90
POST_MIN_CITATION_COMPLETENESS_SCORE = 85
POST_MIN_INTENT_SCORE = 80
POST_MIN_COVERAGE_SCORE = 80
POST_MIN_CONTRADICTION_SCORE = 80
POST_MIN_SCORE_GAIN = 5.0

PostControllerEdge = Literal[
    "accept",
    "targeted_synthesis",
    "targeted_evidence",
    "partial_replan",
    "full_replan",
    "stop_incomplete",
]

_POST_SCORE_WEIGHTS = {
    "intent_alignment": 0.15,
    "material_claim_grounding": 0.20,
    "citation_fidelity": 0.15,
    "citation_completeness": 0.10,
    "contradiction_handling": 0.10,
    "coverage": 0.15,
    "coherence": 0.10,
    "limitations_calibration": 0.05,
}


@dataclass(frozen=True)
class _PostAssessment:
    weighted_score: float
    issue_ids: tuple[str, ...]
    major_issue_ids: tuple[str, ...]
    affected_ids: tuple[str, ...]
    target_segment_ids: tuple[str, ...]
    plan_issue_ids: tuple[str, ...]
    evidence_issue_ids: tuple[str, ...]
    synthesis_issue_ids: tuple[str, ...]
    has_contract_violation: bool
    has_unacceptable_claim: bool
    material_claim_audit_missing: bool
    acceptable: bool


def _budget_from_state(state: DeepResearchState) -> BudgetSnapshot | None:
    raw = state.get("budget_snapshot")
    if isinstance(raw, BudgetSnapshot):
        return raw.model_copy(deep=True)
    if raw is None:
        return BudgetSnapshot()
    if isinstance(raw, dict):
        try:
            return BudgetSnapshot.model_validate(raw)
        except (TypeError, ValueError, ValidationError):
            return None
    return None


def _run_from_state(state: DeepResearchState) -> PostSynthesisEvaluationRun | None:
    raw = state.get("post_synthesis_evaluation_run")
    if isinstance(raw, PostSynthesisEvaluationRun):
        return raw
    if isinstance(raw, dict):
        try:
            return PostSynthesisEvaluationRun.model_validate(raw)
        except (TypeError, ValueError, ValidationError):
            return None
    return None


def _candidate_report(state: DeepResearchState) -> ResearchReport | None:
    raw = state.get("candidate_report")
    if isinstance(raw, ResearchReport):
        return raw
    if isinstance(raw, dict):
        try:
            return ResearchReport.model_validate(raw)
        except (TypeError, ValueError, ValidationError):
            return None
    return None


def _current_report_subject(
    state: DeepResearchState,
) -> tuple[ResearchReport, str, int] | None:
    report = _candidate_report(state)
    version = state.get("report_version")
    if (
        report is None
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
    ):
        return None
    return report, report_digest(report), version


def _valid_post_history(
    state: DeepResearchState,
) -> list[PostSynthesisRoutingDecision] | None:
    raw_history = state.get("post_routing_history", [])
    if not isinstance(raw_history, list):
        return None
    history: list[PostSynthesisRoutingDecision] = []
    for raw in raw_history:
        if isinstance(raw, PostSynthesisRoutingDecision):
            history.append(raw)
            continue
        if isinstance(raw, dict):
            try:
                history.append(PostSynthesisRoutingDecision.model_validate(raw))
                continue
            except (TypeError, ValueError, ValidationError):
                return None
        return None
    return history


def _questions_from_state(state: DeepResearchState) -> list[SubQuestion] | None:
    raw_questions = state.get("sub_questions", [])
    if not isinstance(raw_questions, list):
        return None
    try:
        questions = [SubQuestion.model_validate(item) for item in raw_questions]
    except (TypeError, ValueError, ValidationError):
        return None
    question_ids = [question.id.strip() for question in questions]
    if (
        not questions
        or any(not question_id for question_id in question_ids)
        or len(question_ids) != len(set(question_ids))
    ):
        return None
    return questions


def _reports_from_state(state: DeepResearchState) -> list[SubReport] | None:
    raw_reports = state.get("sub_reports", [])
    if not isinstance(raw_reports, list):
        return None
    try:
        reports = [SubReport.model_validate(item) for item in raw_reports]
    except (TypeError, ValueError, ValidationError):
        return None
    report_ids = [report.sub_question_id.strip() for report in reports]
    if (
        not reports
        or any(not report_id for report_id in report_ids)
        or len(report_ids) != len(set(report_ids))
    ):
        return None
    return reports


def _fingerprints_from_state(state: DeepResearchState) -> set[str] | None:
    raw = state.get("post_recovery_fingerprints", [])
    if not isinstance(raw, list) or any(
        not isinstance(item, str) or not item for item in raw
    ):
        return None
    return set(raw)


def _weighted_score(scores) -> float:
    return round(
        sum(
            getattr(scores, score_name) * weight
            for score_name, weight in _POST_SCORE_WEIGHTS.items()
        ),
        2,
    )


def _affected_priority_ratio(
    questions: list[SubQuestion],
    affected_ids: tuple[str, ...],
) -> float:
    active_by_id = {question.id: question for question in questions}

    def weight(question: SubQuestion) -> int:
        return 3 if question.priority <= 1 else 2 if question.priority == 2 else 1

    total_weight = sum(weight(question) for question in questions)
    affected_weight = sum(
        weight(active_by_id[identifier])
        for identifier in affected_ids
        if identifier in active_by_id
    )
    return affected_weight / total_weight if total_weight else 0.0


def _contains_synthesis_failure(report: ResearchReport) -> bool:
    if not report.sections:
        return True
    markers = (
        "section generation failed",
        "report outline generation failed",
        "synthesis section error",
    )
    return any(
        not section.content.strip()
        or any(marker in section.content.casefold() for marker in markers)
        for section in report.sections
    )


def _assessment(state: DeepResearchState) -> _PostAssessment | None:
    run = _run_from_state(state)
    subject = _current_report_subject(state)
    if (
        run is None
        or run.status != "completed"
        or run.evaluation is None
        or subject is None
    ):
        return None
    report, current_digest, current_version = subject
    if (
        run.report_digest != current_digest
        or run.report_version != current_version
        or _contains_synthesis_failure(report)
    ):
        return None

    evaluation = run.evaluation
    active_questions = _questions_from_state(state)
    reports = _reports_from_state(state)
    if active_questions is None or reports is None:
        return None
    active_ids = [question.id for question in active_questions]
    report_ids = [report.sub_question_id for report in reports]
    if set(report_ids) != set(active_ids):
        return None
    active_id_set = set(active_ids)

    try:
        sources, evidence = build_evidence_inventory(reports)
        validate_post_synthesis_reference_contract(
            evaluation,
            report=report,
            active_ids=active_ids,
            sources=sources,
            evidence=evidence,
        )
    except Exception:
        return None

    known_segment_ids = {segment.id for segment in build_report_segments(report)}
    audit_ids = [audit.segment_id for audit in evaluation.segment_audits]
    if len(audit_ids) != len(set(audit_ids)) or set(audit_ids) != known_segment_ids:
        return None

    all_claims = [
        claim
        for audit in evaluation.segment_audits
        for claim in audit.claims
    ]
    claim_ids = [claim.claim_id for claim in all_claims]
    if len(claim_ids) != len(set(claim_ids)):
        return None
    required_claim_segment_ids = {
        segment.id
        for segment in build_report_segments(report)
        if segment.component in {"executive_summary", "section", "key_finding"}
    }
    audited_claim_segment_ids = {
        audit.segment_id
        for audit in evaluation.segment_audits
        if audit.contains_material_claims and audit.claims
    }
    material_claim_audit_missing = bool(
        required_claim_segment_ids - audited_claim_segment_ids
    )
    has_unacceptable_claim = any(
        claim.support
        in {
            "partially_supported",
            "unsupported",
            "contradicted",
            "unverifiable",
        }
        or claim.calibration != "accurate"
        or not claim.evidence_refs
        or claim.citation.status != "correct"
        or not claim.citation.cited_source_ids
        for claim in all_claims
    )

    issue_ids = [issue.id for issue in evaluation.issues]
    if len(issue_ids) != len(set(issue_ids)):
        return None
    if any(
        set(issue.segment_ids) - known_segment_ids
        or set(issue.claim_ids) - set(claim_ids)
        or set(issue.affected_sub_question_ids) - active_id_set
        for issue in evaluation.issues
    ):
        return None

    plan_issues = [
        issue
        for issue in evaluation.issues
        if issue.suggested_repair_stage == "plan"
        or issue.category == "contract_violation"
    ]
    evidence_issues = [
        issue
        for issue in evaluation.issues
        if issue not in plan_issues
        and (
            issue.suggested_repair_stage == "evidence"
            or issue.category in {"unsupported_claim", "contradicted_claim"}
        )
    ]
    synthesis_issues = [
        issue
        for issue in evaluation.issues
        if issue not in plan_issues and issue not in evidence_issues
    ]
    affected_ids = tuple(
        question_id
        for question_id in active_ids
        if any(
            question_id in issue.affected_sub_question_ids
            for issue in evaluation.issues
        )
    )
    target_segment_ids = tuple(
        segment_id
        for segment_id in known_segment_ids
        if any(segment_id in issue.segment_ids for issue in evaluation.issues)
    )
    major_issue_ids = tuple(
        sorted(
            issue.id
            for issue in evaluation.issues
            if issue.severity in {"major", "blocker"}
        )
    )
    scores = evaluation.scores
    weighted_score = _weighted_score(scores)
    critical_scores_pass = (
        scores.intent_alignment >= POST_MIN_INTENT_SCORE
        and scores.material_claim_grounding >= POST_MIN_GROUNDING_SCORE
        and scores.citation_fidelity >= POST_MIN_CITATION_FIDELITY_SCORE
        and scores.citation_completeness >= POST_MIN_CITATION_COMPLETENESS_SCORE
        and scores.contradiction_handling >= POST_MIN_CONTRADICTION_SCORE
        and scores.coverage >= POST_MIN_COVERAGE_SCORE
    )
    acceptable = (
        weighted_score >= POST_ACCEPT_OVERALL_SCORE
        and critical_scores_pass
        and not major_issue_ids
        and not has_unacceptable_claim
        and not material_claim_audit_missing
    )
    return _PostAssessment(
        weighted_score=weighted_score,
        issue_ids=tuple(sorted(issue_ids)),
        major_issue_ids=major_issue_ids,
        affected_ids=affected_ids,
        target_segment_ids=target_segment_ids,
        plan_issue_ids=tuple(sorted(issue.id for issue in plan_issues)),
        evidence_issue_ids=tuple(sorted(issue.id for issue in evidence_issues)),
        synthesis_issue_ids=tuple(sorted(issue.id for issue in synthesis_issues)),
        has_contract_violation=any(
            issue.category == "contract_violation" for issue in plan_issues
        ),
        has_unacceptable_claim=has_unacceptable_claim,
        material_claim_audit_missing=material_claim_audit_missing,
        acceptable=acceptable,
    )


def post_synthesis_evaluation_is_acceptable(state: DeepResearchState) -> bool:
    """Recompute the authoritative final-report quality predicate.

    Persisted controller decisions are routing receipts, not proof that the
    bound evaluator output passed.  Every publication boundary calls this
    function so a forged or corrupted ``accept`` decision cannot turn a
    genuine failing evaluation into a publishable report.
    """

    assessment = _assessment(state)
    return assessment is not None and assessment.acceptable


def _fingerprint(
    state: DeepResearchState,
    *,
    route: PostSynthesisRoute,
    stage: RepairStage,
    issue_ids: tuple[str, ...],
) -> str:
    report = _candidate_report(state)
    payload = {
        "candidate_report": report.model_dump() if report is not None else None,
        "issue_ids": list(issue_ids),
        "plan_version": state.get("plan_version", 1),
        "route": route,
        "stage": stage.value,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _next_budget(
    current: BudgetSnapshot,
    *,
    route: PostSynthesisRoute,
    stage: RepairStage,
) -> BudgetSnapshot:
    budget = current.model_copy(deep=True)
    budget.post_evaluations_used = min(
        budget.post_evaluation_limit,
        budget.post_evaluations_used + 1,
    )
    if route == "targeted_repair" and stage == RepairStage.SYNTHESIS:
        budget.synthesis_repairs_used += 1
    elif route == "targeted_repair" and stage == RepairStage.EVIDENCE:
        budget.targeted_repairs_used += 1
    elif route == "partial_replan":
        budget.partial_replans_used += 1
    elif route == "full_replan":
        budget.full_replans_used += 1
    if route in {"targeted_repair", "partial_replan", "full_replan"}:
        budget.total_recoveries_used += 1
    return budget


def _decision(
    state: DeepResearchState,
    *,
    route: PostSynthesisRoute,
    stage: RepairStage,
    reason_code: str,
    reason: str,
    budget: BudgetSnapshot,
    assessment: _PostAssessment | None,
    affected_ids: tuple[str, ...] = (),
    target_segment_ids: tuple[str, ...] = (),
    score_gain: float | None = None,
    closed_major_ids: tuple[str, ...] = (),
    escalated_from: PostSynthesisRoute | None = None,
) -> PostSynthesisRoutingDecision:
    issue_ids = assessment.issue_ids if assessment else ()
    major_issue_ids = assessment.major_issue_ids if assessment else ()
    active_questions = _questions_from_state(state) or []
    ratio = round(_affected_priority_ratio(active_questions, affected_ids), 4)
    fingerprint = _fingerprint(
        state,
        route=route,
        stage=stage,
        issue_ids=issue_ids,
    )
    subject = _current_report_subject(state)
    current_run = _run_from_state(state)
    return PostSynthesisRoutingDecision(
        route=route,
        repair_stage=stage,
        reason_code=reason_code,
        reason=reason,
        affected_sub_question_ids=list(affected_ids),
        target_report_segment_ids=list(target_segment_ids),
        issue_ids=list(issue_ids),
        major_issue_ids=list(major_issue_ids),
        weighted_overall_score=(assessment.weighted_score if assessment else 0.0),
        affected_priority_ratio=ratio,
        score_gain=score_gain,
        closed_major_issue_ids=list(closed_major_ids),
        fingerprint=fingerprint,
        escalated_from=escalated_from,
        budget=_next_budget(budget, route=route, stage=stage),
        report_digest=subject[1] if subject is not None else None,
        report_version=subject[2] if subject is not None else None,
        evaluation_digest=(
            evaluation_digest(current_run) if current_run is not None else None
        ),
    )


def decide_post_synthesis_route(
    state: DeepResearchState,
) -> PostSynthesisRoutingDecision:
    """Return one deterministic five-level route from the report evaluator artifact."""
    budget = _budget_from_state(state)
    history = _valid_post_history(state)
    fingerprints = _fingerprints_from_state(state)
    questions = _questions_from_state(state)
    if budget is None or history is None or fingerprints is None or questions is None:
        return _decision(
            state,
            route="stop_incomplete",
            stage=RepairStage.INITIAL,
            reason_code="invalid_post_controller_state",
            reason="The post-synthesis controller state was invalid.",
            budget=budget or BudgetSnapshot(),
            assessment=None,
        )

    assessment = _assessment(state)
    if budget.post_evaluations_used >= budget.post_evaluation_limit:
        return _decision(
            state,
            route="stop_incomplete",
            stage=RepairStage.INITIAL,
            reason_code="post_evaluation_budget_exhausted",
            reason="The post-synthesis evaluation budget is exhausted.",
            budget=budget,
            assessment=assessment,
        )
    if assessment is None:
        return _decision(
            state,
            route="stop_incomplete",
            stage=RepairStage.INITIAL,
            reason_code="invalid_or_failed_post_evaluation",
            reason="The post-synthesis evaluator did not produce a valid completed assessment.",
            budget=budget,
            assessment=None,
        )
    if assessment.material_claim_audit_missing:
        return _decision(
            state,
            route="stop_incomplete",
            stage=RepairStage.INITIAL,
            reason_code="material_claim_audit_missing",
            reason="No material claims were audited on the substantive report surfaces.",
            budget=budget,
            assessment=assessment,
        )
    if assessment.acceptable:
        return _decision(
            state,
            route="accept",
            stage=RepairStage.INITIAL,
            reason_code="post_quality_gate_passed",
            reason="All deterministic post-synthesis quality gates passed.",
            budget=budget,
            assessment=assessment,
        )

    affected_ids = assessment.affected_ids
    target_segments = assessment.target_segment_ids
    affected_ratio = _affected_priority_ratio(questions, affected_ids)
    if assessment.plan_issue_ids:
        if assessment.has_contract_violation or affected_ratio > 0.50:
            route: PostSynthesisRoute = "full_replan"
            stage = RepairStage.FULL_REPLAN
            affected_ids = tuple(question.id for question in questions)
            reason_code = "post_plan_contract_failure"
            reason = "The report audit found a global research-plan contract failure."
        elif affected_ids:
            route = "partial_replan"
            stage = RepairStage.PARTIAL_REPLAN
            reason_code = "post_plan_branch_failure"
            reason = "The report audit found a bounded plan branch failure."
        else:
            route = "stop_incomplete"
            stage = RepairStage.INITIAL
            reason_code = "missing_post_plan_targets"
            reason = "Plan repair was requested without valid affected sub-questions."
    elif assessment.evidence_issue_ids:
        if affected_ids and affected_ratio > 0.50:
            route = "full_replan"
            stage = RepairStage.FULL_REPLAN
            affected_ids = tuple(question.id for question in questions)
            reason_code = "post_evidence_scope_global"
            reason = "The report audit found evidence gaps across most weighted plan scope."
        elif affected_ids and affected_ratio > 0.25:
            route = "partial_replan"
            stage = RepairStage.PARTIAL_REPLAN
            reason_code = "post_evidence_scope_partial"
            reason = "The report audit found evidence gaps across a bounded plan scope."
        elif len(affected_ids) == 1:
            route = "targeted_repair"
            stage = RepairStage.EVIDENCE
            reason_code = "post_evidence_gap"
            reason = "The report audit found a localized low-weight evidence gap."
        elif affected_ids:
            route = "targeted_repair"
            stage = RepairStage.EVIDENCE
            reason_code = "post_evidence_gap"
            reason = "The report audit found localized low-weight evidence gaps."
        else:
            route = "stop_incomplete"
            stage = RepairStage.INITIAL
            reason_code = "missing_post_evidence_targets"
            reason = "Evidence repair was requested without valid affected sub-questions."
    elif assessment.synthesis_issue_ids:
        if target_segments:
            route = "targeted_repair"
            stage = RepairStage.SYNTHESIS
            reason_code = "post_synthesis_defect"
            reason = "The evidence is sufficient but authorized report segments need revision."
        else:
            route = "stop_incomplete"
            stage = RepairStage.INITIAL
            reason_code = "missing_post_synthesis_targets"
            reason = "Synthesis repair was requested without valid report segments."
    else:
        route = "stop_incomplete"
        stage = RepairStage.INITIAL
        reason_code = "unroutable_post_quality_failure"
        reason = "Post-synthesis quality gates failed without a valid repair scope."

    recovery_routes = {"targeted_repair", "partial_replan", "full_replan"}
    score_gain: float | None = None
    closed_major_ids: tuple[str, ...] = ()
    escalated_from: PostSynthesisRoute | None = None
    if route in recovery_routes and history:
        previous = history[-1]
        score_gain = round(
            assessment.weighted_score - previous.weighted_overall_score,
            2,
        )
        closed_major_ids = tuple(
            sorted(set(previous.major_issue_ids) - set(assessment.major_issue_ids))
        )

    fingerprint = _fingerprint(
        state,
        route=route,
        stage=stage,
        issue_ids=assessment.issue_ids,
    )
    repeated = fingerprint in fingerprints
    no_gain = (
        route in recovery_routes
        and history
        and score_gain is not None
        and score_gain < POST_MIN_SCORE_GAIN
        and not closed_major_ids
    )
    if route in recovery_routes and (repeated or no_gain):
        escalated_from = route
        if route == "targeted_repair" and stage == RepairStage.EVIDENCE and affected_ids:
            route = "partial_replan"
            stage = RepairStage.PARTIAL_REPLAN
            reason_code = "post_recovery_no_gain"
            reason = "Repeated evidence repair made no material quality gain and was escalated."
        elif route == "partial_replan":
            route = "full_replan"
            stage = RepairStage.FULL_REPLAN
            affected_ids = tuple(question.id for question in questions)
            reason_code = "post_recovery_no_gain"
            reason = "Repeated partial replanning made no material quality gain and was escalated."
        else:
            route = "stop_incomplete"
            stage = RepairStage.INITIAL
            reason_code = "post_recovery_no_gain"
            reason = "The post-synthesis recovery loop did not converge."

    if route in recovery_routes and budget.total_recoveries_used >= budget.total_recovery_limit:
        route = "stop_incomplete"
        stage = RepairStage.INITIAL
        reason_code = "total_recovery_budget_exhausted"
        reason = "The total research recovery budget is exhausted."
    elif route == "targeted_repair" and stage == RepairStage.SYNTHESIS and (
        budget.synthesis_repairs_used >= budget.synthesis_repair_limit
    ):
        route = "stop_incomplete"
        stage = RepairStage.INITIAL
        reason_code = "synthesis_repair_budget_exhausted"
        reason = "The synthesis revision budget is exhausted."
    elif route == "targeted_repair" and stage == RepairStage.EVIDENCE and (
        budget.targeted_repairs_used >= budget.targeted_repair_limit
    ):
        escalated_from = escalated_from or "targeted_repair"
        if budget.partial_replans_used < budget.partial_replan_limit:
            route = "partial_replan"
            stage = RepairStage.PARTIAL_REPLAN
            reason_code = "evidence_repair_budget_escalated"
            reason = (
                "The targeted evidence budget is exhausted; repair escalated "
                "to partial replanning."
            )
        elif budget.full_replans_used < budget.full_replan_limit:
            route = "full_replan"
            stage = RepairStage.FULL_REPLAN
            affected_ids = tuple(question.id for question in questions)
            reason_code = "evidence_repair_budget_escalated"
            reason = (
                "The targeted and partial budgets are exhausted; repair "
                "escalated to full replanning."
            )
        else:
            route = "stop_incomplete"
            stage = RepairStage.INITIAL
            reason_code = "evidence_repair_budget_exhausted"
            reason = "All applicable evidence-repair budgets are exhausted."
    elif route == "partial_replan" and budget.partial_replans_used >= budget.partial_replan_limit:
        escalated_from = escalated_from or "partial_replan"
        if budget.full_replans_used < budget.full_replan_limit:
            route = "full_replan"
            stage = RepairStage.FULL_REPLAN
            affected_ids = tuple(question.id for question in questions)
            reason_code = "partial_replan_budget_escalated"
            reason = (
                "The partial replanning budget is exhausted; repair escalated "
                "to full replanning."
            )
        else:
            route = "stop_incomplete"
            stage = RepairStage.INITIAL
            reason_code = "partial_replan_budget_exhausted"
            reason = "The partial and full replanning budgets are exhausted."
    elif route == "full_replan" and budget.full_replans_used >= budget.full_replan_limit:
        route = "stop_incomplete"
        stage = RepairStage.INITIAL
        reason_code = "full_replan_budget_exhausted"
        reason = "The full replanning budget is exhausted."

    return _decision(
        state,
        route=route,
        stage=stage,
        reason_code=reason_code,
        reason=reason,
        budget=budget,
        assessment=assessment,
        affected_ids=affected_ids,
        target_segment_ids=target_segments,
        score_gain=score_gain,
        closed_major_ids=closed_major_ids,
        escalated_from=escalated_from,
    )


def post_synthesis_controller_node(state: DeepResearchState) -> dict:
    decision = decide_post_synthesis_route(state)
    raw_history = state.get("post_routing_history", [])
    history = list(raw_history) if isinstance(raw_history, list) else []
    history.append(decision)
    raw_fingerprints = state.get("post_recovery_fingerprints", [])
    fingerprints = (
        [item for item in raw_fingerprints if isinstance(item, str) and item]
        if isinstance(raw_fingerprints, list)
        else []
    )
    if (
        decision.route in {"targeted_repair", "partial_replan", "full_replan"}
        and decision.fingerprint not in fingerprints
    ):
        fingerprints.append(decision.fingerprint)

    update = {
        "post_synthesis_controller_decision": decision,
        "controller_decision": decision,
        "post_routing_history": history,
        "post_recovery_fingerprints": fingerprints,
        "repair_stage": decision.repair_stage,
        "budget_snapshot": decision.budget,
        "target_report_segment_ids": list(decision.target_report_segment_ids),
        "final_report": None,
        "report_accepted": False,
    }
    if decision.repair_stage == RepairStage.EVIDENCE:
        update["execution_target_ids"] = list(decision.affected_sub_question_ids)
    if decision.route == "stop_incomplete":
        update.update(
            {
                "terminal_status": "incomplete",
                "terminal_reason": decision.reason,
                "workflow_error_code": decision.reason_code,
            }
        )
    logger.info(
        "post_synthesis_route_selected",
        route=decision.route,
        repair_stage=decision.repair_stage.value,
        reason_code=decision.reason_code,
        score=decision.weighted_overall_score,
        budget=decision.budget.model_dump(),
    )
    return update


def route_after_post_controller(state: DeepResearchState) -> PostControllerEdge:
    raw = state.get("post_synthesis_controller_decision")
    if isinstance(raw, dict):
        try:
            raw = PostSynthesisRoutingDecision.model_validate(raw)
        except (TypeError, ValueError, ValidationError):
            return "stop_incomplete"
    if not isinstance(raw, PostSynthesisRoutingDecision):
        return "stop_incomplete"
    if raw.route != "stop_incomplete":
        subject = _current_report_subject(state)
        run = _run_from_state(state)
        if (
            subject is None
            or run is None
            or run.status != "completed"
            or run.evaluation is None
            or raw.report_digest != subject[1]
            or raw.report_version != subject[2]
            or run.report_digest != subject[1]
            or run.report_version != subject[2]
            or raw.evaluation_digest != evaluation_digest(run)
        ):
            return "stop_incomplete"
    if raw.route == "targeted_repair":
        if raw.repair_stage == RepairStage.SYNTHESIS:
            return "targeted_synthesis"
        if raw.repair_stage == RepairStage.EVIDENCE:
            return "targeted_evidence"
        return "stop_incomplete"
    if raw.route == "accept" and not post_synthesis_evaluation_is_acceptable(state):
        return "stop_incomplete"
    if raw.route in {
        "accept",
        "partial_replan",
        "full_replan",
        "stop_incomplete",
    }:
        return raw.route
    return "stop_incomplete"


__all__ = [
    "POST_ACCEPT_OVERALL_SCORE",
    "decide_post_synthesis_route",
    "post_synthesis_evaluation_is_acceptable",
    "post_synthesis_controller_node",
    "route_after_post_controller",
]
