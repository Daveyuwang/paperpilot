from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Literal

import structlog
from langgraph.runtime import Runtime

from app.deep_research.context import DeepResearchContext
from app.deep_research.llm_factory import make_structured_llm
from app.deep_research.models import RepairPlan, RoutingDecision, SubQuestion
from app.deep_research.prompts import (
    FULL_REPLAN_SYSTEM,
    FULL_REPLAN_USER,
    PARTIAL_REPLAN_SYSTEM,
    PARTIAL_REPLAN_USER,
)
from app.deep_research.provenance import report_digest
from app.deep_research.state import DeepResearchState

logger = structlog.get_logger()

REPAIR_PLANNER_TIMEOUT_SECONDS = 90


@dataclass(frozen=True)
class _RepairDecision:
    route: str
    affected_sub_question_ids: list[str]
    evaluation_phase: Literal["pre_synthesis", "post_synthesis"]
    repair_stage: str
    issue_ids: list[str]
    target_report_segment_ids: list[str]
    report_digest: str | None
    report_version: int | None


def _decision(state: DeepResearchState) -> _RepairDecision | None:
    value = state.get("controller_decision")
    if isinstance(value, RoutingDecision):
        phase = getattr(value, "evaluation_phase", "pre_synthesis")
        if phase not in {"pre_synthesis", "post_synthesis"}:
            return None
        raw_stage = getattr(value, "repair_stage", "")
        stage = getattr(raw_stage, "value", raw_stage)
        return _RepairDecision(
            route=value.route,
            affected_sub_question_ids=list(value.affected_sub_question_ids),
            evaluation_phase=phase,
            repair_stage=stage if isinstance(stage, str) else "",
            issue_ids=list(getattr(value, "issue_ids", [])),
            target_report_segment_ids=list(
                getattr(value, "target_report_segment_ids", [])
            ),
            report_digest=getattr(value, "report_digest", None),
            report_version=getattr(value, "report_version", None),
        )
    if isinstance(value, dict):
        route = value.get("route")
        targets = value.get(
            "affected_sub_question_ids",
            value.get("target_sub_question_ids"),
        )
        phase = value.get("evaluation_phase", "pre_synthesis")
        raw_stage = value.get("repair_stage", "")
        stage = getattr(raw_stage, "value", raw_stage)
        issue_ids = value.get("issue_ids", [])
        segment_ids = value.get("target_report_segment_ids", [])
        subject_digest = value.get("report_digest")
        subject_version = value.get("report_version")
        if route in {"targeted_repair", "partial_replan", "full_replan"} and isinstance(
            targets, list
        ) and phase in {"pre_synthesis", "post_synthesis"} and isinstance(
            issue_ids, list
        ) and isinstance(segment_ids, list):
            return _RepairDecision(
                route=route,
                affected_sub_question_ids=list(targets),
                evaluation_phase=phase,
                repair_stage=stage if isinstance(stage, str) else "",
                issue_ids=list(issue_ids),
                target_report_segment_ids=list(segment_ids),
                report_digest=(
                    subject_digest if isinstance(subject_digest, str) else None
                ),
                report_version=(
                    subject_version
                    if isinstance(subject_version, int)
                    and not isinstance(subject_version, bool)
                    else None
                ),
            )
    return None


def _failed_preparation(error_code: str, reason: str) -> dict:
    return {
        "repair_preparation_status": "failed",
        "workflow_error_code": error_code,
        "terminal_reason": reason,
        "terminal_status": "incomplete",
        "execution_target_ids": None,
    }


def _validate_targets(
    active_questions: list[SubQuestion],
    target_ids: list[str],
) -> list[str] | None:
    active_ids = [question.id for question in active_questions]
    if any(not isinstance(target_id, str) for target_id in target_ids):
        return None
    active_id_set = set(active_ids)
    if (
        not active_ids
        or len(active_ids) != len(active_id_set)
        or not target_ids
        or len(target_ids) != len(set(target_ids))
        or not set(target_ids).issubset(active_id_set)
        or any(not target_id for target_id in target_ids)
    ):
        return None
    target_set = set(target_ids)
    return [question_id for question_id in active_ids if question_id in target_set]


def _active_questions(state: DeepResearchState) -> list[SubQuestion] | None:
    raw_questions = state.get("sub_questions")
    if not isinstance(raw_questions, list):
        return None
    questions: list[SubQuestion] = []
    for raw_question in raw_questions:
        try:
            questions.append(SubQuestion.model_validate(raw_question))
        except (TypeError, ValueError):
            return None
    return questions


def _dedupe_queries(queries: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for query in queries:
        cleaned = query.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            output.append(cleaned)
        if len(output) == 3:
            break
    return output


def _field(value, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _completed_post_evaluation(state: DeepResearchState):
    run = state.get("post_synthesis_evaluation_run")
    if _field(run, "status") != "completed":
        return None
    if _field(run, "evaluation") is None:
        return None
    return run


def _candidate_title(state: DeepResearchState) -> str | None:
    candidate = state.get("candidate_report")
    title = _field(candidate, "title")
    return title if isinstance(title, str) and title.strip() else None


def _string_list(value) -> list[str] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return None
    return list(value)


def _matching_post_evaluation(
    state: DeepResearchState,
    decision: _RepairDecision,
    active_questions: list[SubQuestion],
    target_ids: list[str],
):
    """Return the current post audit only when its repair scope is authoritative."""
    if decision.evaluation_phase != "post_synthesis":
        return None
    expected_stage = {
        "targeted_repair": "evidence",
        "partial_replan": "partial_replan",
        "full_replan": "full_replan",
    }.get(decision.route)
    if decision.repair_stage != expected_stage:
        return None

    run = _completed_post_evaluation(state)
    evaluation = _field(run, "evaluation")
    issues = _field(evaluation, "issues")
    if run is None or evaluation is None or not isinstance(issues, list) or not issues:
        return None

    candidate = state.get("candidate_report")
    current_version = state.get("report_version")
    if (
        candidate is None
        or not isinstance(current_version, int)
        or isinstance(current_version, bool)
        or current_version < 1
    ):
        return None
    try:
        current_digest = report_digest(candidate)
    except (TypeError, ValueError):
        return None
    if (
        _field(run, "report_digest") != current_digest
        or _field(run, "report_version") != current_version
        or decision.report_digest != current_digest
        or decision.report_version != current_version
    ):
        return None

    active_ids = [question.id for question in active_questions]
    active_id_set = set(active_ids)
    issue_ids: list[str] = []
    affected_id_set: set[str] = set()
    segment_id_set: set[str] = set()
    for issue in issues:
        issue_id = _field(issue, "id")
        affected_ids = _string_list(_field(issue, "affected_sub_question_ids"))
        segment_ids = _string_list(_field(issue, "segment_ids"))
        if (
            not isinstance(issue_id, str)
            or not issue_id
            or affected_ids is None
            or segment_ids is None
            or len(affected_ids) != len(set(affected_ids))
            or len(segment_ids) != len(set(segment_ids))
            or set(affected_ids) - active_id_set
        ):
            return None
        issue_ids.append(issue_id)
        affected_id_set.update(affected_ids)
        segment_id_set.update(segment_ids)

    decision_issue_ids = _string_list(decision.issue_ids)
    decision_segment_ids = _string_list(decision.target_report_segment_ids)
    if (
        decision_issue_ids is None
        or decision_segment_ids is None
        or len(issue_ids) != len(set(issue_ids))
        or len(decision_issue_ids) != len(set(decision_issue_ids))
        or set(decision_issue_ids) != set(issue_ids)
        or len(decision_segment_ids) != len(set(decision_segment_ids))
        or set(decision_segment_ids) != segment_id_set
        or _candidate_title(state) is None
    ):
        return None

    expected_targets = (
        active_ids
        if decision.route == "full_replan"
        else [identifier for identifier in active_ids if identifier in affected_id_set]
    )
    if not expected_targets or target_ids != expected_targets:
        return None
    return evaluation


def _post_suggested_queries(
    evaluation,
    target_ids: list[str],
) -> dict[str, list[str]]:
    suggested_by_id: dict[str, list[str]] = {target_id: [] for target_id in target_ids}
    for issue in _field(evaluation, "issues", []):
        issue_targets = _field(issue, "affected_sub_question_ids", [])
        # ``suggested_queries`` is optional in the current report-audit schema and
        # intentionally read through getattr for forward/backward compatibility.
        suggestions = getattr(issue, "suggested_queries", [])
        if isinstance(issue, dict):
            suggestions = issue.get("suggested_queries", suggestions)
        if not isinstance(issue_targets, list) or not isinstance(suggestions, list):
            continue
        clean_suggestions = [item for item in suggestions if isinstance(item, str)]
        for target_id in issue_targets:
            if target_id in suggested_by_id:
                suggested_by_id[target_id].extend(clean_suggestions)
    return suggested_by_id


def _post_repair_queries(suggested: list[str], original: list[str]) -> list[str]:
    """Prefer new diagnosis queries while retaining prior search intent."""
    original_queries = _dedupe_queries(original)
    suggested_queries = _dedupe_queries(suggested)
    if not suggested_queries:
        return original_queries
    suggested_limit = 2 if original_queries else 3
    return _dedupe_queries(suggested_queries[:suggested_limit] + original_queries)


def prepare_targeted_repair_node(state: DeepResearchState) -> dict:
    """Prepare a validated one-shot retry batch without changing plan version."""
    decision = _decision(state)
    if decision is None or decision.route != "targeted_repair":
        return _failed_preparation(
            "invalid_targeted_repair_decision",
            "Targeted repair was requested without a valid controller decision.",
        )

    questions = _active_questions(state)
    if questions is None:
        return _failed_preparation(
            "invalid_repair_targets",
            "Targeted repair requires a valid active sub-question plan.",
        )
    target_ids = _validate_targets(questions, decision.affected_sub_question_ids)
    if target_ids is None:
        return _failed_preparation(
            "invalid_repair_targets",
            "Targeted repair requires unique active sub-question IDs.",
        )

    if decision.evaluation_phase == "post_synthesis":
        evaluation = _matching_post_evaluation(
            state,
            decision,
            questions,
            target_ids,
        )
        if evaluation is None:
            return _failed_preparation(
                "invalid_post_repair_context",
                "Post-synthesis evidence repair requires a current matching evaluation.",
            )
        suggested_by_id = _post_suggested_queries(evaluation, target_ids)
    else:
        evaluation_run = state.get("pre_synthesis_evaluation_run")
        evaluation = (
            evaluation_run.evaluation
            if evaluation_run is not None and evaluation_run.status == "completed"
            else None
        )
        suggested_by_id = {target_id: [] for target_id in target_ids}
        if evaluation is not None:
            for directive in evaluation.repair_directives:
                for target_id in directive.target_sub_question_ids:
                    if target_id in suggested_by_id:
                        suggested_by_id[target_id].extend(directive.suggested_queries)

    updated_questions: list[SubQuestion] = []
    target_set = set(target_ids)
    for question in questions:
        if question.id not in target_set:
            updated_questions.append(question)
            continue
        suggested = suggested_by_id.get(question.id, [])
        queries = (
            _post_repair_queries(suggested, list(question.search_queries))
            if decision.evaluation_phase == "post_synthesis"
            else _dedupe_queries(suggested + list(question.search_queries))
        )
        if not queries:
            return _failed_preparation(
                "missing_repair_queries",
                "Targeted repair has no valid query for an affected sub-question.",
            )
        updated_questions.append(question.model_copy(update={"search_queries": queries}))

    return {
        "sub_questions": updated_questions,
        "execution_target_ids": target_ids,
        "repair_preparation_status": "ready",
        "workflow_error_code": None,
        "terminal_reason": None,
        "terminal_status": None,
    }


# Compatibility for callers that adopted the shorter name during step 1.
targeted_repair_node = prepare_targeted_repair_node


def _pre_repair_context_json(state: DeepResearchState, decision: _RepairDecision) -> str:
    evaluation_run = state.get("pre_synthesis_evaluation_run")
    evaluation = (
        evaluation_run.evaluation
        if evaluation_run is not None and evaluation_run.status == "completed"
        else None
    )
    payload = {
        "affected_sub_question_ids": list(decision.affected_sub_question_ids),
        "current_plan": [
            {
                "id": question.id,
                "priority": question.priority,
                "question": question.question,
                "rationale": question.rationale,
                "search_queries": list(question.search_queries),
            }
            for question in (_active_questions(state) or [])
        ],
        "evaluator_issues": [
            {
                "affected_sub_question_ids": list(issue.affected_sub_question_ids),
                "category": issue.category,
                "description": issue.description,
                "id": issue.id,
                "severity": issue.severity,
            }
            for issue in (evaluation.issues if evaluation is not None else [])
        ],
        "failed_steps": [
            {
                "error_code": failure.get("error_code", "unknown"),
                "sub_question_id": failure.get("sub_question_id", ""),
            }
            for failure in state.get("failed_queries", [])
        ],
        "plan_version": state.get("plan_version", 1),
        "repair_directives": [
            {
                "acceptance_criteria": list(directive.acceptance_criteria),
                "id": directive.id,
                "issue_ids": list(directive.issue_ids),
                "objective": directive.objective,
                "suggested_queries": list(directive.suggested_queries),
                "target_sub_question_ids": list(directive.target_sub_question_ids),
            }
            for directive in (evaluation.repair_directives if evaluation is not None else [])
        ],
        "route": decision.route,
        "topic": state.get("topic", ""),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_RAW_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|authorization|password|secret|token)\b"
    r"\s*(?:=|:)\s*[^\s,;]+"
)
_KEY_TOKEN_RE = re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{6,}\b")
_POST_SCORE_FIELDS = (
    "intent_alignment",
    "material_claim_grounding",
    "citation_fidelity",
    "citation_completeness",
    "contradiction_handling",
    "coverage",
    "coherence",
    "limitations_calibration",
)


def _sanitize_text(value, *, max_length: int = 1200) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = _RAW_URL_RE.sub("[redacted-url]", value)
    cleaned = _BEARER_RE.sub("[redacted-credential]", cleaned)
    cleaned = _SECRET_ASSIGNMENT_RE.sub("[redacted-credential]", cleaned)
    cleaned = _KEY_TOKEN_RE.sub("[redacted-credential]", cleaned)
    return " ".join(cleaned.split())[:max_length]


def _sanitized_strings(value, *, max_items: int = 32) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value[:max_items]:
        cleaned = _sanitize_text(item)
        if cleaned:
            output.append(cleaned)
    return output


def _post_score_summary(evaluation) -> dict[str, int | float]:
    scores = _field(evaluation, "scores")
    output: dict[str, int | float] = {}
    for field_name in _POST_SCORE_FIELDS:
        value = _field(scores, field_name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            output[field_name] = value
    return output


def _post_repair_context_json(
    state: DeepResearchState,
    decision: _RepairDecision,
    evaluation,
) -> str:
    candidate = state.get("candidate_report")
    report_version = state.get("report_version", 1)
    if not isinstance(report_version, int) or isinstance(report_version, bool):
        report_version = 1
    plan_version = state.get("plan_version", 1)
    if not isinstance(plan_version, int) or isinstance(plan_version, bool):
        plan_version = 1

    issues = []
    for issue in _field(evaluation, "issues", []):
        suggested_queries = getattr(issue, "suggested_queries", [])
        if isinstance(issue, dict):
            suggested_queries = issue.get("suggested_queries", suggested_queries)
        issues.append(
            {
                "acceptance_criteria": _sanitized_strings(
                    _field(issue, "acceptance_criteria", [])
                ),
                "affected_sub_question_ids": _sanitized_strings(
                    _field(issue, "affected_sub_question_ids", [])
                ),
                "category": _sanitize_text(_field(issue, "category"), max_length=80),
                "claim_ids": _sanitized_strings(_field(issue, "claim_ids", [])),
                "description": _sanitize_text(_field(issue, "description")),
                "id": _sanitize_text(_field(issue, "id"), max_length=128),
                "segment_ids": _sanitized_strings(_field(issue, "segment_ids", [])),
                "severity": _sanitize_text(_field(issue, "severity"), max_length=32),
                "suggested_queries": _sanitized_strings(suggested_queries),
                "suggested_repair_stage": _sanitize_text(
                    _field(issue, "suggested_repair_stage"),
                    max_length=32,
                ),
            }
        )

    payload = {
        "affected_sub_question_ids": _sanitized_strings(
            decision.affected_sub_question_ids
        ),
        "candidate_report": {
            "target_segment_ids": _sanitized_strings(
                decision.target_report_segment_ids
            ),
            "title": _sanitize_text(_field(candidate, "title"), max_length=300),
            "version": report_version,
        },
        "current_plan": [
            {
                "id": _sanitize_text(question.id, max_length=256),
                "priority": question.priority,
                "question": _sanitize_text(question.question),
                "rationale": _sanitize_text(question.rationale),
                "search_queries": _sanitized_strings(list(question.search_queries)),
            }
            for question in (_active_questions(state) or [])
        ],
        "failed_steps": [
            {
                "error_code": _sanitize_text(
                    failure.get("error_code", "unknown"),
                    max_length=80,
                ),
                "sub_question_id": _sanitize_text(
                    failure.get("sub_question_id", ""),
                    max_length=256,
                ),
            }
            for failure in state.get("failed_queries", [])
        ],
        "plan_version": plan_version,
        "post_evaluation_issues": issues,
        "post_evaluation_scores": _post_score_summary(evaluation),
        "route": decision.route,
        "topic": _sanitize_text(state.get("topic", "")),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _repair_context_json(state: DeepResearchState, decision: _RepairDecision) -> str:
    if decision.evaluation_phase == "pre_synthesis":
        return _pre_repair_context_json(state, decision)

    questions = _active_questions(state)
    if questions is None:
        raise ValueError("repair context requires a valid active plan")
    target_ids = _validate_targets(questions, decision.affected_sub_question_ids)
    evaluation = (
        _matching_post_evaluation(state, decision, questions, target_ids)
        if target_ids is not None
        else None
    )
    if evaluation is None:
        raise ValueError("post-synthesis repair context is stale or mismatched")
    return _post_repair_context_json(state, decision, evaluation)


async def _invoke_repair_planner(structured_llm, messages):
    return await asyncio.wait_for(
        structured_llm.ainvoke(messages),
        timeout=REPAIR_PLANNER_TIMEOUT_SECONDS,
    )


def _validated_questions(raw_plan, *, forbidden_ids: set[str]) -> list[SubQuestion]:
    payload = raw_plan.model_dump() if hasattr(raw_plan, "model_dump") else raw_plan
    plan = RepairPlan.model_validate(payload)
    questions = list(plan.sub_questions)
    ids = [question.id.strip() for question in questions]
    if (
        not questions
        or any(not question_id for question_id in ids)
        or len(ids) != len(set(ids))
        or set(ids) & forbidden_ids
        or any(not _dedupe_queries(list(question.search_queries)) for question in questions)
    ):
        raise ValueError("repair planner returned an invalid or conflicting plan")
    return [
        question.model_copy(
            update={
                "id": question.id.strip(),
                "search_queries": _dedupe_queries(list(question.search_queries)),
            }
        )
        for question in questions
    ]


async def partial_replan_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    decision = _decision(state)
    if decision is None or decision.route != "partial_replan":
        return _failed_preparation(
            "invalid_partial_replan_decision",
            "Partial replanning was requested without a valid controller decision.",
        )

    current_questions = _active_questions(state)
    if current_questions is None:
        return _failed_preparation(
            "invalid_repair_targets",
            "Partial replanning requires a valid active sub-question plan.",
        )
    target_ids = _validate_targets(current_questions, decision.affected_sub_question_ids)
    if target_ids is None:
        return _failed_preparation(
            "invalid_repair_targets",
            "Partial replanning requires unique active sub-question IDs.",
        )
    target_set = set(target_ids)
    unaffected_questions = [q for q in current_questions if q.id not in target_set]
    unaffected_ids = {q.id for q in unaffected_questions}

    try:
        repair_context_json = _repair_context_json(state, decision)
    except ValueError:
        return _failed_preparation(
            "invalid_post_repair_context",
            "Post-synthesis partial replanning requires a current matching evaluation.",
        )

    try:
        structured_llm = make_structured_llm(
            state,
            RepairPlan,
            runtime=runtime,
            max_tokens=2000,
            temperature=0.0,
        )
        raw_plan = await _invoke_repair_planner(
            structured_llm,
            [
                {"role": "system", "content": PARTIAL_REPLAN_SYSTEM},
                {
                    "role": "user",
                    "content": PARTIAL_REPLAN_USER.format(
                        repair_context_json=repair_context_json
                    ),
                },
            ],
        )
        replacements = _validated_questions(raw_plan, forbidden_ids=unaffected_ids)
    except Exception as exc:
        logger.error("partial_replan_failed", error=str(exc))
        return _failed_preparation(
            "partial_replan_failed",
            "Partial replanning failed before a replacement branch was ready.",
        )

    replacement_ids = [question.id for question in replacements]
    return {
        "sub_questions": unaffected_questions + replacements,
        "sub_reports": [
            report
            for report in state.get("sub_reports", [])
            if report.sub_question_id in unaffected_ids
        ],
        "failed_queries": [
            failure
            for failure in state.get("failed_queries", [])
            if failure.get("sub_question_id") in unaffected_ids
        ],
        "execution_target_ids": replacement_ids,
        "plan_version": state.get("plan_version", 1) + 1,
        "repair_preparation_status": "ready",
        "workflow_error_code": None,
        "terminal_reason": None,
        "terminal_status": None,
    }


async def full_replan_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    decision = _decision(state)
    if decision is None or decision.route != "full_replan":
        return _failed_preparation(
            "invalid_full_replan_decision",
            "Full replanning was requested without a valid controller decision.",
        )
    current_questions = _active_questions(state)
    if not current_questions:
        return _failed_preparation(
            "missing_active_plan",
            "Full replanning requires an active plan to replace.",
        )
    if _validate_targets(
        current_questions,
        decision.affected_sub_question_ids,
    ) is None:
        return _failed_preparation(
            "invalid_repair_targets",
            "Full replanning requires unique active affected sub-question IDs.",
        )

    try:
        repair_context_json = _repair_context_json(state, decision)
    except ValueError:
        return _failed_preparation(
            "invalid_post_repair_context",
            "Post-synthesis full replanning requires a current matching evaluation.",
        )

    try:
        structured_llm = make_structured_llm(
            state,
            RepairPlan,
            runtime=runtime,
            max_tokens=2500,
            temperature=0.0,
        )
        raw_plan = await _invoke_repair_planner(
            structured_llm,
            [
                {"role": "system", "content": FULL_REPLAN_SYSTEM},
                {
                    "role": "user",
                    "content": FULL_REPLAN_USER.format(
                        repair_context_json=repair_context_json
                    ),
                },
            ],
        )
        replacement = _validated_questions(raw_plan, forbidden_ids=set())
    except Exception as exc:
        logger.error("full_replan_failed", error=str(exc))
        return _failed_preparation(
            "full_replan_failed",
            "Full replanning failed before a replacement plan was ready.",
        )

    return {
        "sub_questions": replacement,
        "sub_reports": [],
        "failed_queries": [],
        "final_report": None,
        "execution_target_ids": [question.id for question in replacement],
        "plan_version": state.get("plan_version", 1) + 1,
        "repair_preparation_status": "ready",
        "workflow_error_code": None,
        "terminal_reason": None,
        "terminal_status": None,
    }


def route_after_repair_preparation(
    state: DeepResearchState,
) -> Literal["execute", "stop_incomplete"]:
    if (
        state.get("repair_preparation_status") == "ready"
        and state.get("execution_target_ids")
    ):
        return "execute"
    return "stop_incomplete"
