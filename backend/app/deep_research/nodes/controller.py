from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import structlog

from app.deep_research.models import (
    BudgetSnapshot,
    PostSynthesisEvaluationRun,
    PostSynthesisRoutingDecision,
    PreSynthesisEvaluationRun,
    PreSynthesisRoute,
    RepairStage,
    ResearchReport,
    RoutingDecision,
)
from app.deep_research.provenance import evaluation_digest, report_digest
from app.deep_research.state import DeepResearchState

logger = structlog.get_logger()

ACCEPT_OVERALL_SCORE = 85.0
MIN_CRITICAL_SCORE = 75
MIN_EVIDENCE_SCORE = 70
MIN_SCORE_GAIN = 5.0

_SCORE_WEIGHTS = {
    "intent_alignment": 0.15,
    "must_answer_coverage": 0.20,
    "source_relevance": 0.10,
    "source_quality": 0.10,
    "source_diversity": 0.05,
    "source_recency": 0.05,
    "grounding_consistency": 0.15,
    "contradiction_handling": 0.10,
    "synthesis_readiness": 0.10,
}
_RECOVERY_ROUTES: tuple[PreSynthesisRoute, ...] = (
    "targeted_repair",
    "partial_replan",
    "full_replan",
)


@dataclass(frozen=True)
class _Assessment:
    weighted_score: float
    issue_ids: tuple[str, ...]
    major_issue_ids: tuple[str, ...]
    affected_ids: tuple[str, ...]
    affected_ratio: float
    missing_report_ids: tuple[str, ...]
    global_structural_mismatch: bool
    acceptable: bool


def _budget_from_state(state: DeepResearchState) -> BudgetSnapshot | None:
    current = state.get("budget_snapshot")
    if isinstance(current, BudgetSnapshot):
        return current.model_copy(deep=True)
    if isinstance(current, dict):
        try:
            return BudgetSnapshot.model_validate(current)
        except (TypeError, ValueError):
            return None
    if current is not None:
        return None
    return BudgetSnapshot()


def _weighted_score(scores) -> float:
    numerator = sum(
        getattr(scores, field) * weight for field, weight in _SCORE_WEIGHTS.items()
    )
    return round(numerator / sum(_SCORE_WEIGHTS.values()), 2)


def _priority_weight(priority: int) -> int:
    if priority <= 1:
        return 3
    if priority == 2:
        return 2
    return 1


def _corpus_hash(state: DeepResearchState) -> str:
    payload = {
        "failed_queries": [
            {
                "error_code": failure.get("error_code", "unknown"),
                "sub_question_id": failure.get("sub_question_id", ""),
            }
            for failure in state.get("failed_queries", [])
        ],
        "sub_questions": [
            {
                "id": question.id,
                "priority": question.priority,
                "question": question.question,
                "search_queries": list(question.search_queries),
            }
            for question in state.get("sub_questions", [])
        ],
        "sub_reports": [
            {
                "confidence": report.confidence,
                "findings": report.findings,
                "gaps": report.gaps,
                "key_facts": list(report.key_facts),
                "sources": [
                    {"title": source.title, "url": source.url}
                    for source in report.sources
                ],
                "sub_question_id": report.sub_question_id,
            }
            for report in state.get("sub_reports", [])
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fingerprint(
    state: DeepResearchState,
    route: PreSynthesisRoute,
    issue_ids: tuple[str, ...],
) -> str:
    payload = {
        "action": route,
        "corpus_hash": _corpus_hash(state),
        "issue_ids": list(issue_ids),
        "plan_version": state.get("plan_version", 1),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _assessment(state: DeepResearchState) -> _Assessment | None:
    raw_run = state.get("pre_synthesis_evaluation_run")
    if isinstance(raw_run, PreSynthesisEvaluationRun):
        run = raw_run
    elif isinstance(raw_run, dict):
        try:
            run = PreSynthesisEvaluationRun.model_validate(raw_run)
        except (TypeError, ValueError):
            return None
    else:
        return None
    if run is None or run.status != "completed" or run.evaluation is None:
        return None

    evaluation = run.evaluation
    active_questions = state.get("sub_questions", [])
    active_ids = [question.id for question in active_questions]
    active_id_set = set(active_ids)
    if not active_ids or len(active_ids) != len(active_id_set):
        return None
    assessed_ids = list(evaluation.assessed_sub_question_ids)
    if (
        len(assessed_ids) != len(set(assessed_ids))
        or set(assessed_ids) != active_id_set
    ):
        return None

    reports = state.get("sub_reports", [])
    report_id_list = [report.sub_question_id for report in reports]
    report_ids = set(report_id_list)
    if (
        len(report_id_list) != len(report_ids)
        or not report_ids.issubset(active_id_set)
        or "" in report_ids
    ):
        return None
    missing_report_ids = tuple(sorted(active_id_set - report_ids))
    failure_ids = {
        failure.get("sub_question_id", "")
        for failure in state.get("failed_queries", [])
    }

    issue_ids = tuple(sorted(issue.id for issue in evaluation.issues))
    major_issue_ids = tuple(
        sorted(
            issue.id
            for issue in evaluation.issues
            if issue.severity in {"major", "blocker"}
        )
    )
    global_structural_mismatch = any(
        issue.severity in {"major", "blocker"}
        and issue.category in {"intent_mismatch", "plan_structure"}
        for issue in evaluation.issues
    )

    affected_ids = {
        question_id
        for issue in evaluation.issues
        for question_id in issue.affected_sub_question_ids
    }
    affected_ids.update(
        question_id
        for directive in evaluation.repair_directives
        for question_id in directive.target_sub_question_ids
    )
    affected_ids.update(missing_report_ids)
    affected_ids.update(failure_ids)

    if affected_ids - active_id_set or "" in affected_ids:
        return None

    total_weight = sum(_priority_weight(question.priority) for question in active_questions)
    affected_weight = sum(
        _priority_weight(question.priority)
        for question in active_questions
        if question.id in affected_ids
    )
    affected_ratio = round(affected_weight / total_weight, 4) if total_weight else 0.0

    scores = evaluation.scores
    weighted = _weighted_score(scores)
    critical_pass = all(
        getattr(scores, field) >= MIN_CRITICAL_SCORE
        for field in (
            "intent_alignment",
            "must_answer_coverage",
            "grounding_consistency",
            "synthesis_readiness",
        )
    )
    evidence_pass = all(
        getattr(scores, field) >= MIN_EVIDENCE_SCORE
        for field in (
            "source_relevance",
            "source_quality",
            "contradiction_handling",
        )
    )
    acceptable = (
        weighted >= ACCEPT_OVERALL_SCORE
        and critical_pass
        and evidence_pass
        and not major_issue_ids
        and not state.get("failed_queries", [])
        and not missing_report_ids
        and all(
            any(source.url.strip() for source in report.sources)
            for report in reports
            if next(
                question.priority
                for question in active_questions
                if question.id == report.sub_question_id
            ) <= 1
        )
    )
    return _Assessment(
        weighted_score=weighted,
        issue_ids=issue_ids,
        major_issue_ids=major_issue_ids,
        affected_ids=tuple(sorted(affected_ids)),
        affected_ratio=affected_ratio,
        missing_report_ids=missing_report_ids,
        global_structural_mismatch=global_structural_mismatch,
        acceptable=acceptable,
    )


def _escalate(route: PreSynthesisRoute) -> PreSynthesisRoute:
    return {
        "targeted_repair": "partial_replan",
        "partial_replan": "full_replan",
        "full_replan": "stop_incomplete",
        "accept": "accept",
        "stop_incomplete": "stop_incomplete",
    }[route]


def _stage_for(route: PreSynthesisRoute) -> RepairStage:
    return {
        "accept": RepairStage.INITIAL,
        "targeted_repair": RepairStage.TARGETED_REPAIR,
        "partial_replan": RepairStage.PARTIAL_REPLAN,
        "full_replan": RepairStage.FULL_REPLAN,
        "stop_incomplete": RepairStage.INITIAL,
    }[route]


def _apply_budget_limits(
    route: PreSynthesisRoute,
    budget: BudgetSnapshot,
) -> tuple[PreSynthesisRoute, PreSynthesisRoute | None]:
    original = route
    if route not in _RECOVERY_ROUTES:
        return route, None
    if budget.total_recoveries_used >= budget.total_recovery_limit:
        return "stop_incomplete", original

    while route in _RECOVERY_ROUTES:
        exhausted = (
            route == "targeted_repair"
            and budget.targeted_repairs_used >= budget.targeted_repair_limit
        ) or (
            route == "partial_replan"
            and budget.partial_replans_used >= budget.partial_replan_limit
        ) or (
            route == "full_replan"
            and budget.full_replans_used >= budget.full_replan_limit
        )
        if not exhausted:
            break
        route = _escalate(route)
    return route, original if route != original else None


def _budget_after_decision(
    current: BudgetSnapshot,
    route: PreSynthesisRoute,
) -> BudgetSnapshot:
    update = current.model_copy(deep=True)
    update.pre_evaluations_used = min(
        update.pre_evaluation_limit,
        update.pre_evaluations_used + 1,
    )
    if route == "targeted_repair":
        update.targeted_repairs_used += 1
    elif route == "partial_replan":
        update.partial_replans_used += 1
    elif route == "full_replan":
        update.full_replans_used += 1
    if route in _RECOVERY_ROUTES:
        update.total_recoveries_used += 1
    return update


def decide_pre_synthesis_route(state: DeepResearchState) -> RoutingDecision:
    """Return a pure, deterministic five-level decision from evaluator output and state."""
    parsed_budget = _budget_from_state(state)
    budget = parsed_budget or BudgetSnapshot()
    try:
        assessment = _assessment(state)
    except (AttributeError, TypeError, ValueError):
        assessment = None
    history = state.get("routing_history", [])

    weighted_score = assessment.weighted_score if assessment else 0.0
    issue_ids = assessment.issue_ids if assessment else ()
    major_issue_ids = assessment.major_issue_ids if assessment else ()
    affected_ids = assessment.affected_ids if assessment else ()
    affected_ratio = assessment.affected_ratio if assessment else 0.0
    score_gain: float | None = None
    closed_major_ids: tuple[str, ...] = ()
    escalated_from: PreSynthesisRoute | None = None

    if parsed_budget is None:
        route: PreSynthesisRoute = "stop_incomplete"
        reason_code = "invalid_controller_budget"
        reason = "The controller budget snapshot was invalid."
    elif budget.pre_evaluations_used >= budget.pre_evaluation_limit:
        route = "stop_incomplete"
        reason_code = "pre_evaluation_budget_exhausted"
        reason = "The pre-synthesis evaluation budget is exhausted."
    elif assessment is None:
        route = "stop_incomplete"
        reason_code = "invalid_or_failed_evaluation"
        reason = "The semantic evaluator did not produce a valid completed assessment."
    elif assessment.acceptable:
        route = "accept"
        reason_code = "quality_gate_passed"
        reason = "All deterministic pre-synthesis quality gates passed."
    else:
        if assessment.global_structural_mismatch or assessment.affected_ratio > 0.5:
            route = "full_replan"
            reason_code = "global_or_majority_scope_failure"
            reason = "The plan is structurally mismatched or more than half of its weighted scope is affected."
            affected_ids = tuple(sorted(question.id for question in state.get("sub_questions", [])))
            affected_ratio = 1.0
        elif assessment.affected_ratio > 0.25:
            route = "partial_replan"
            reason_code = "branch_scope_failure"
            reason = "More than 25% and at most 50% of weighted plan scope requires repair."
        elif assessment.affected_ids:
            route = "targeted_repair"
            reason_code = "localized_evidence_failure"
            reason = "At most 25% of weighted plan scope requires localized evidence repair."
        else:
            route = "stop_incomplete"
            reason_code = "missing_repair_targets"
            reason = "Quality gates failed without a valid non-empty repair target."

        if history and route in _RECOVERY_ROUTES:
            previous = history[-1]
            if isinstance(previous, dict):
                try:
                    previous = RoutingDecision.model_validate(previous)
                except ValueError:
                    previous = None
        else:
            previous = None
        if route in _RECOVERY_ROUTES:
            prior_fingerprints = state.get(
                "recovery_fingerprints",
                [],
            )
            repeated = _fingerprint(state, route, issue_ids) in set(prior_fingerprints)
            no_gain = False
        else:
            repeated = False
            no_gain = False
        if previous is not None and route in _RECOVERY_ROUTES:
            previous_major = set(previous.major_issue_ids)
            closed_major_ids = tuple(sorted(previous_major - set(major_issue_ids)))
            score_gain = round(weighted_score - previous.weighted_overall_score, 2)
            no_gain = score_gain < MIN_SCORE_GAIN and not closed_major_ids
        if route in _RECOVERY_ROUTES and (repeated or no_gain):
            original = route
            route = _escalate(route)
            escalated_from = original
            reason_code = "repeated_or_no_gain"
            reason = "Recovery repeated unchanged work or gained less than five points without closing a major issue."

        limited_route, budget_escalated_from = _apply_budget_limits(route, budget)
        if budget_escalated_from is not None:
            escalated_from = escalated_from or budget_escalated_from
            route = limited_route
            reason_code = "recovery_budget_escalation"
            reason = "The requested recovery level is exhausted and was deterministically escalated."

    if route in {"targeted_repair", "partial_replan"} and not affected_ids:
        route = "stop_incomplete"
        reason_code = "missing_repair_targets"
        reason = "A repair route cannot run without valid affected sub-question IDs."

    fingerprint = _fingerprint(state, route, issue_ids)
    next_budget = _budget_after_decision(budget, route)
    return RoutingDecision(
        route=route,
        repair_stage=_stage_for(route),
        reason_code=reason_code,
        reason=reason,
        affected_sub_question_ids=list(affected_ids),
        issue_ids=list(issue_ids),
        major_issue_ids=list(major_issue_ids),
        weighted_overall_score=weighted_score,
        affected_priority_ratio=affected_ratio,
        score_gain=score_gain,
        closed_major_issue_ids=list(closed_major_ids),
        fingerprint=fingerprint,
        escalated_from=escalated_from,
        budget=next_budget,
    )


def controller_node(state: DeepResearchState) -> dict:
    decision = decide_pre_synthesis_route(state)
    history = list(state.get("routing_history", []))
    history.append(decision)
    fingerprints = list(state.get("recovery_fingerprints", []))
    if decision.route in _RECOVERY_ROUTES and decision.fingerprint not in fingerprints:
        fingerprints.append(decision.fingerprint)

    logger.info(
        "pre_synthesis_route_selected",
        route=decision.route,
        reason_code=decision.reason_code,
        score=decision.weighted_overall_score,
        affected_ratio=decision.affected_priority_ratio,
        budget=decision.budget.model_dump(),
    )
    update = {
        "controller_decision": decision,
        "routing_history": history,
        "repair_stage": decision.repair_stage,
        "budget_snapshot": decision.budget,
        "recovery_fingerprints": fingerprints,
    }
    if decision.route == "stop_incomplete":
        update.update(
            {
                "terminal_status": "incomplete",
                "terminal_reason": decision.reason,
                "workflow_error_code": decision.reason_code,
            }
        )
    return update


def route_after_controller(state: DeepResearchState) -> PreSynthesisRoute:
    decision = state.get("controller_decision")
    if not isinstance(decision, RoutingDecision):
        return "stop_incomplete"
    return decision.route


def finalize_incomplete_node(state: DeepResearchState) -> dict:
    decision = state.get("post_synthesis_controller_decision") or state.get(
        "controller_decision"
    )
    reason = state.get("terminal_reason")
    if not reason and isinstance(decision, RoutingDecision):
        reason = decision.reason
    return {
        "terminal_status": "incomplete",
        "terminal_reason": reason or "Research stopped before synthesis.",
        "execution_target_ids": None,
        "final_report": None,
        "report_accepted": False,
    }


def finalize_complete_node(state: DeepResearchState) -> dict:
    """Publish a candidate only after the authoritative report gate accepts it."""
    # Import lazily to keep the pre-synthesis controller independently usable
    # while sharing one authoritative post-synthesis acceptance predicate.
    from app.deep_research.nodes.post_controller import (
        post_synthesis_evaluation_is_acceptable,
    )

    raw_run = state.get("post_synthesis_evaluation_run")
    if isinstance(raw_run, dict):
        try:
            raw_run = PostSynthesisEvaluationRun.model_validate(raw_run)
        except (TypeError, ValueError):
            raw_run = None

    raw_decision = state.get("post_synthesis_controller_decision")
    if isinstance(raw_decision, dict):
        try:
            raw_decision = PostSynthesisRoutingDecision.model_validate(raw_decision)
        except (TypeError, ValueError):
            raw_decision = None

    raw_candidate = state.get("candidate_report")
    if isinstance(raw_candidate, dict):
        try:
            raw_candidate = ResearchReport.model_validate(raw_candidate)
        except (TypeError, ValueError):
            raw_candidate = None

    raw_final = state.get("final_report")
    if isinstance(raw_final, dict):
        try:
            raw_final = ResearchReport.model_validate(raw_final)
        except (TypeError, ValueError):
            raw_final = None

    report_version = state.get("report_version")
    candidate_digest = (
        report_digest(raw_candidate)
        if isinstance(raw_candidate, ResearchReport)
        else None
    )
    existing_final_matches = (
        state.get("final_report") is None
        or (
            isinstance(raw_final, ResearchReport)
            and report_digest(raw_final) == candidate_digest
        )
    )

    if (
        not isinstance(raw_run, PostSynthesisEvaluationRun)
        or raw_run.status != "completed"
        or raw_run.evaluation is None
        or not isinstance(raw_decision, PostSynthesisRoutingDecision)
        or raw_decision.route != "accept"
        or raw_decision.repair_stage != RepairStage.INITIAL
        or not isinstance(raw_candidate, ResearchReport)
        or not isinstance(report_version, int)
        or isinstance(report_version, bool)
        or report_version < 1
        or candidate_digest is None
        or raw_run.report_digest != candidate_digest
        or raw_run.report_version != report_version
        or raw_decision.report_digest != candidate_digest
        or raw_decision.report_version != report_version
        or raw_decision.evaluation_digest != evaluation_digest(raw_run)
        or not post_synthesis_evaluation_is_acceptable(state)
        or not existing_final_matches
    ):
        logger.error("finalize_complete_rejected_invalid_gate_state")
        return {
            "terminal_status": "incomplete",
            "terminal_reason": "The candidate report did not pass the final quality gate.",
            "workflow_error_code": "invalid_final_quality_gate",
            "execution_target_ids": None,
            "final_report": None,
            "report_accepted": False,
        }

    return {
        "terminal_status": "completed",
        "terminal_reason": None,
        "workflow_error_code": None,
        "execution_target_ids": None,
        "final_report": raw_candidate,
        "report_accepted": True,
    }
