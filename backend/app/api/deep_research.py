import json
import re
import uuid
import time
import structlog
import asyncio
from datetime import datetime
from enum import Enum
from collections.abc import Mapping
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.background import BackgroundTask

from app.api.guest import require_guest_id
from app.api.sources import DiscoveredSource as DiscoveredSourceModel
from app.db.postgres import AsyncSessionLocal
from app.rate_limit import limiter
from app.api.drafts import _resolve_llm

from app.deep_research.artifact_recorder import PostgresArtifactRecorder
from app.deep_research.artifacts import (
    get_artifact_snapshot,
    list_artifact_versions,
)
from app.deep_research.context import (
    DEEP_RESEARCH_GRAPH_VERSION,
    DeepResearchContext,
)
from app.deep_research.console_protocol import (
    DurableRunEventWriter,
    artifact_ref,
    budget_payload,
    serialize_run_event,
    sse_run_event,
    utc_iso,
)
from app.deep_research.events import (
    DEFAULT_EVENT_PAGE_SIZE,
    MAX_EVENT_PAGE_SIZE,
    list_run_events,
)
from app.deep_research.run_lock import (
    DeepResearchRunAlreadyActive,
    DeepResearchRunLockUnavailable,
    deep_research_run_lock,
)
from app.deep_research.runtime import (
    DeepResearchCheckpointAlreadyExists,
    DeepResearchCheckpointNotFound,
    DeepResearchGraphVersionMismatch,
    DeepResearchRuntimeError,
    DeepResearchRuntimeNotInitialized,
    get_deep_research_runtime,
)
from app.deep_research.state import DeepResearchState
from app.deep_research.models import (
    BudgetSnapshot,
    Plan,
    PreSynthesisEvaluationRun,
    PostSynthesisEvaluationRun,
    PostSynthesisRoutingDecision,
    RepairStage,
    ResearchReport,
    ReportSection,
    RoutingDecision,
    SubQuestion,
    SubReport,
)
from app.deep_research.config import DEPTH_CONFIG
from app.deep_research.prompts import PLAN_SYSTEM, PLAN_USER
from app.deep_research.llm_factory import make_structured_llm as make_dr_structured_llm
from app.deep_research.provenance import (
    build_report_segments,
    corpus_digest,
    evaluation_digest,
    report_digest,
)
from app.deep_research.nodes.post_controller import (
    post_synthesis_evaluation_is_acceptable,
)
from app.llm.client import LLMClient
from app.models.schemas import (
    DeepResearchArtifactVersionOut,
    DeepResearchResumeRequest,
)
from app.workflow_state import (
    create_workflow_run,
    get_owned_workflow_run,
    get_owned_workspace,
    mark_workflow_run_running,
    stop_owned_deep_research_run,
    update_workflow_stage,
)
from app.models.orm import (
    DeepResearchArtifactKind,
    DeepResearchArtifactVersion,
    WorkflowRun,
    WorkflowRunType,
    WorkflowRunStatus,
)
from app.tracing import create_trace, get_langfuse_callback_handler

logger = structlog.get_logger()
router = APIRouter()

# ── Request models (unchanged — frontend contract) ──────────────────────────

class DeepResearchInput(BaseModel):
    topic: str
    focus: str | None = None
    time_horizon: str = "broad"
    output_length: str = "medium"
    use_workspace_sources: bool = True
    discover_new_sources: bool = True
    must_include: str | None = None
    must_exclude: str | None = None
    notes: str | None = None
    target_deliverable_id: str | None = None
    depth: str = "standard"  # quick | standard | deep


class DRSourcePayload(BaseModel):
    id: str
    title: str
    authors: list[str] = []
    year: int | None = None
    abstract: str | None = None
    provider: str = ""
    paper_id: str | None = None
    label: str = "maybe"


class DRSectionPayload(BaseModel):
    id: str
    title: str
    content: str
    order: int
    linkedSourceIds: list[str] = []


class PrePlanSubQuestion(BaseModel):
    id: str
    question: str
    search_queries: list[str] = []
    priority: int = 1
    rationale: str = ""


class PrePlan(BaseModel):
    sub_questions: list[PrePlanSubQuestion]
    depth: str = "standard"


class DeepResearchRequest(BaseModel):
    input: DeepResearchInput
    workspace_id: str
    workspace_sources: list[DRSourcePayload] = []
    existing_sections: list[DRSectionPayload] = []
    active_paper_id: str | None = None
    pre_plan: PrePlan | None = None


# ── Generate-plan request/response ────────────────────────────────────────────

class GeneratePlanRequest(BaseModel):
    topic: str
    workspace_id: str
    workspace_sources: list[DRSourcePayload] = []
    active_paper_id: str | None = None


class SubQuestionPreview(BaseModel):
    id: str
    question: str
    rationale: str
    search_queries: list[str] = []
    priority: int = 1


class GeneratePlanResponse(BaseModel):
    sub_questions: list[SubQuestionPreview]
    overall_approach: str
    recommended_depth: str
    sources_strategy: str
    focus_note: str | None = None


# ── Response models (unchanged — frontend contract) ─────────────────────────

class ClarificationQuestion(BaseModel):
    field: str
    question: str
    suggestion: str | None = None


class DRSectionUpdate(BaseModel):
    section_index: int
    title: str
    mode: str
    generated_content: str
    source_ids_used: list[str]
    notes: str | None = None


class FollowUpItem(BaseModel):
    title: str
    description: str | None = None
    category: str | None = None
    priority: int = 50


class DeepResearchRunResult(BaseModel):
    run_id: str
    status: str
    clarification_questions: list[ClarificationQuestion] = []
    generated_title: str | None = None
    generated_outline: list[str] | None = None
    section_updates: list[DRSectionUpdate] = []
    discovered_sources: list[DiscoveredSourceModel] = []
    saved_source_ids: list[str] = []
    selected_source_ids: list[str] = []
    unresolved_questions: list[str] = []
    follow_up_items: list[FollowUpItem] = []
    summary: str | None = None
    message: str | None = None
    terminal_reason: str | None = None
    candidate_diagnostics: dict[str, Any] | None = None


# ── Validation (kept from original) ─────────────────────────────────────────

def _validate_and_clarify(req: DeepResearchRequest) -> list[ClarificationQuestion]:
    questions: list[ClarificationQuestion] = []
    topic = req.input.topic.strip()

    if not topic:
        questions.append(ClarificationQuestion(
            field="topic",
            question="What research topic or question would you like to investigate?",
        ))
        return questions

    words = topic.split()
    if len(words) <= 2 and not req.input.focus:
        questions.append(ClarificationQuestion(
            field="focus",
            question=f'"{topic}" is quite broad. What specific aspect or angle do you want to focus on?',
            suggestion=f"e.g. {topic} in the context of ..., or {topic} for ...",
        ))

    if not req.input.use_workspace_sources and not req.input.discover_new_sources:
        questions.append(ClarificationQuestion(
            field="sources",
            question="Both source options are disabled. Enable at least one.",
        ))

    return questions


async def _llm_validate_topic(req: DeepResearchRequest, client: LLMClient) -> list[ClarificationQuestion]:
    """Use LLM to evaluate if the topic is specific enough for research."""
    topic = req.input.topic.strip()
    focus = req.input.focus or ""
    output_length = req.input.output_length

    prompt = f"""Evaluate this research request for clarity and feasibility.

Topic: {topic}
Focus: {focus or "(none)"}
Output length: {output_length}
Has workspace sources: {req.input.use_workspace_sources}
Discover new sources: {req.input.discover_new_sources}

Decide if this is clear enough to research. Consider:
1. Is the topic specific enough for the requested output length?
2. Are there ambiguous terms that could mean very different things?
3. Is the scope manageable?

If the topic is clear enough, respond with: {{"ok": true}}
If clarification is needed, respond with: {{"ok": false, "questions": [{{"field": "topic_or_focus", "question": "your question", "suggestion": "optional suggestion"}}]}}

Respond ONLY with valid JSON, no other text."""

    try:
        content = await client.create_text(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.1,
        )
        text = content.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        if result.get("ok"):
            return []
        return [
            ClarificationQuestion(
                field=q.get("field", "topic"),
                question=q["question"],
                suggestion=q.get("suggestion"),
            )
            for q in result.get("questions", [])
        ]
    except Exception as exc:
        logger.warning("llm_validate_topic_failed", error_type=type(exc).__name__)
        return []


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_initial_state(req: DeepResearchRequest) -> DeepResearchState:
    topic = req.input.topic.strip()
    if req.input.focus:
        topic = f"{topic} — {req.input.focus.strip()}"
    if req.input.must_include:
        topic += f" (must include: {req.input.must_include.strip()})"

    user_sources = [s.title for s in req.workspace_sources if s.label != "discarded"]

    # If pre_plan provided, convert to SubQuestion models
    pre_sub_questions: list[SubQuestion] = []
    depth = req.input.depth
    if req.pre_plan:
        depth = req.pre_plan.depth or depth
        pre_sub_questions = [
            SubQuestion(
                id=sq.id,
                question=sq.question,
                search_queries=sq.search_queries or [sq.question],
                priority=sq.priority,
                rationale=sq.rationale or "",
            )
            for sq in req.pre_plan.sub_questions
        ]

    return DeepResearchState(
        topic=topic,
        user_sources=user_sources,
        depth=depth,
        sub_questions=pre_sub_questions,
        sub_reports=[],
        failed_queries=[],
        replan_count=0,
        plan_version=1,
        corpus_version=0,
        budget_snapshot=BudgetSnapshot(),
        routing_history=[],
        recovery_fingerprints=[],
        post_evaluation_history=[],
        post_routing_history=[],
        post_recovery_fingerprints=[],
        report_version=0,
        report_accepted=False,
        final_report=None,
    )


def _build_graph_context(
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    llm: LLMClient,
    artifact_recorder: PostgresArtifactRecorder | None = None,
    artifact_persistence_required: bool = False,
) -> DeepResearchContext:
    context = DeepResearchContext(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        api_key=llm.resolved.api_key,
        base_url=llm.resolved.base_url,
        model=llm.resolved.model,
        graph_version=DEEP_RESEARCH_GRAPH_VERSION,
    )
    if artifact_recorder is not None:
        context["artifact_recorder"] = artifact_recorder
    if artifact_persistence_required:
        context["artifact_persistence_required"] = True
    return context


def _report_to_result(
    report: ResearchReport,
    run_id: str,
) -> DeepResearchRunResult:
    section_updates = [
        DRSectionUpdate(
            section_index=i,
            title=sec.heading,
            mode="fill_empty",
            generated_content=sec.content,
            source_ids_used=[],
        )
        for i, sec in enumerate(report.sections)
    ]

    source_urls = [s.url for s in report.sources if s.url]

    return DeepResearchRunResult(
        run_id=run_id,
        status="completed",
        generated_title=report.title,
        generated_outline=[sec.heading for sec in report.sections],
        section_updates=section_updates,
        unresolved_questions=[report.limitations] if report.limitations else [],
        follow_up_items=[
            FollowUpItem(title=f, description="Key finding from research", category="custom")
            for f in report.key_findings[:3]
        ],
        summary=report.executive_summary[:300],
        selected_source_ids=source_urls,
    )


_AUTHORITATIVE_ROUTES = {
    "accept",
    "targeted_repair",
    "partial_replan",
    "full_replan",
    "stop_incomplete",
}

_PRE_SCORE_FIELDS = {
    "intent_alignment",
    "must_answer_coverage",
    "source_relevance",
    "source_quality",
    "source_diversity",
    "source_recency",
    "grounding_consistency",
    "contradiction_handling",
    "synthesis_readiness",
}
_POST_SCORE_FIELDS = {
    "intent_alignment",
    "material_claim_grounding",
    "citation_fidelity",
    "citation_completeness",
    "contradiction_handling",
    "coverage",
    "coherence",
    "limitations_calibration",
}
_PUBLIC_ISSUE_SCALAR_FIELDS = {
    "id",
    "category",
    "severity",
    "suggested_repair_stage",
}
_PUBLIC_ISSUE_ID_LIST_FIELDS = {
    "affected_sub_question_ids",
    "claim_ids",
    "segment_ids",
}
_PUBLIC_BUDGET_FIELDS = {
    "pre_evaluations_used",
    "targeted_repairs_used",
    "partial_replans_used",
    "full_replans_used",
    "total_recoveries_used",
    "post_evaluations_used",
    "synthesis_repairs_used",
    "pre_evaluation_limit",
    "targeted_repair_limit",
    "partial_replan_limit",
    "full_replan_limit",
    "total_recovery_limit",
    "post_evaluation_limit",
    "synthesis_repair_limit",
}
_PUBLIC_REPAIR_STAGES = {
    "initial",
    "targeted_repair",
    "synthesis",
    "evidence",
    "partial_replan",
    "full_replan",
}

_NODE_STAGE_MAP: dict[str, tuple[str, str]] = {
    "plan": ("planning", "Decomposing research topic..."),
    "execute": ("executing", "Investigating sub-questions..."),
    "evaluate": ("evaluating", "Evaluating research evidence..."),
    "controller": ("evaluating", "Selecting the next research action..."),
    "targeted_repair": ("replanning", "Repairing targeted evidence gaps..."),
    "partial_replan": ("replanning", "Replanning affected research branches..."),
    "full_replan": ("replanning", "Rebuilding the research plan..."),
    "replan": ("replanning", "Generating supplementary questions..."),
    "synthesize": ("synthesizing", "Writing a candidate report..."),
    "evaluate_report": ("evaluating", "Auditing report claims and citations..."),
    "post_controller": ("evaluating", "Selecting the report repair action..."),
    "revise_report": ("synthesizing", "Revising the candidate report..."),
    "finalize_complete": ("synthesizing", "Finalizing the accepted report..."),
    "finalize_incomplete": ("evaluating", "Finalizing an incomplete research run..."),
}

_TRACKED_STATE_KEYS = {
    "candidate_report",
    "final_report",
    "report_accepted",
    "report_version",
    "terminal_status",
    "terminal_reason",
    "workflow_error_code",
    "pre_synthesis_evaluation_run",
    "post_synthesis_evaluation_run",
    "controller_decision",
    "post_synthesis_controller_decision",
    "post_controller_decision",
    "budget_snapshot",
}


def _public_value(value: Any) -> Any:
    """Convert typed node output to JSON-safe public data without object reprs."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _public_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def _public_mapping(value: Any) -> dict[str, Any] | None:
    public = _public_value(value)
    return public if isinstance(public, dict) else None


def _safe_public_text(value: Any, fallback: str, *, limit: int = 500) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = " ".join(value.split()).strip()
    lowered = cleaned.lower()
    if any(marker in lowered for marker in ("sk-", "api_key", "authorization:", "bearer ")):
        return fallback
    return cleaned[:limit] if cleaned else fallback


def _safe_public_code(value: Any, *, limit: int = 128) -> str | None:
    if not isinstance(value, str):
        return None
    return value if re.fullmatch(rf"[A-Za-z0-9_.:-]{{1,{limit}}}", value) else None


def _safe_public_number(value: Any, *, minimum: float, maximum: float) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < minimum or value > maximum:
        return None
    return value


def _safe_identifier_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        safe = _safe_public_code(item, limit=256)
        if safe is not None and safe not in result:
            result.append(safe)
    return result


def _safe_scores(value: Any, *, phase: str) -> dict[str, int | float]:
    scores = _public_mapping(value) or {}
    allowed = _PRE_SCORE_FIELDS if phase == "pre_synthesis" else _POST_SCORE_FIELDS
    result: dict[str, int | float] = {}
    for key in sorted(allowed):
        score = _safe_public_number(scores.get(key), minimum=0, maximum=100)
        if score is not None:
            result[key] = score
    return result


def _safe_issues(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    issues: list[dict[str, Any]] = []
    for raw_issue in value:
        issue = _public_mapping(raw_issue)
        if issue is None:
            continue
        public_issue: dict[str, Any] = {}
        for field in _PUBLIC_ISSUE_SCALAR_FIELDS:
            safe = _safe_public_code(issue.get(field))
            if safe is not None:
                public_issue[field] = safe
        for field in _PUBLIC_ISSUE_ID_LIST_FIELDS:
            identifiers = _safe_identifier_list(issue.get(field))
            if identifiers:
                public_issue[field] = identifiers
        source_urls = issue.get("source_urls")
        if isinstance(source_urls, list):
            public_issue["source_count"] = len(source_urls)
        if public_issue.get("id"):
            issues.append(public_issue)
    return issues


def _safe_budget(value: Any) -> dict[str, int]:
    budget = _public_mapping(value) or {}
    result: dict[str, int] = {}
    for key in sorted(_PUBLIC_BUDGET_FIELDS):
        amount = _safe_public_number(budget.get(key), minimum=0, maximum=1_000_000)
        if isinstance(amount, int):
            result[key] = amount
    return result


def _evaluation_event_payload(
    output: Mapping[str, Any],
    *,
    phase: str,
) -> dict[str, Any] | None:
    key = (
        "pre_synthesis_evaluation_run"
        if phase == "pre_synthesis"
        else "post_synthesis_evaluation_run"
    )
    run = _public_mapping(output.get(key))
    if run is None:
        return None

    status = run.get("status")
    if status not in {"completed", "failed"}:
        return None

    evaluation = _public_mapping(run.get("evaluation")) or {}
    scores = _safe_scores(evaluation.get("scores"), phase=phase)
    issues = _safe_issues(evaluation.get("issues"))
    duration_ms = _safe_public_number(run.get("duration_ms"), minimum=0, maximum=86_400_000)
    attempts = _safe_public_number(run.get("attempts"), minimum=0, maximum=10)

    return {
        "phase": phase,
        "status": status,
        "model": _safe_public_text(run.get("evaluator_model"), "unknown", limit=200),
        "duration_ms": duration_ms if duration_ms is not None else 0,
        "attempts": attempts if attempts is not None else 0,
        "error_code": _safe_public_code(run.get("error_code")),
        "scores": scores,
        "issues": issues,
        "issue_count": len(issues),
    }


def _route_event_payload(
    output: Mapping[str, Any],
    *,
    phase: str,
) -> dict[str, Any] | None:
    if phase == "pre_synthesis":
        raw_decision = output.get("controller_decision")
    else:
        raw_decision = output.get("post_synthesis_controller_decision")
        if raw_decision is None:
            raw_decision = output.get("post_controller_decision")
    decision = _public_mapping(raw_decision)
    if decision is None or decision.get("route") not in _AUTHORITATIVE_ROUTES:
        return None
    decision_phase = decision.get("evaluation_phase")
    if decision_phase in {"pre_synthesis", "post_synthesis"} and decision_phase != phase:
        return None

    target_sub_questions = _safe_identifier_list(decision.get("affected_sub_question_ids"))
    target_segments = _safe_identifier_list(decision.get("target_report_segment_ids"))

    budget = _safe_budget(decision.get("budget"))
    if not budget:
        budget = _safe_budget(output.get("budget_snapshot"))

    repair_stage = decision.get("repair_stage")
    if repair_stage not in _PUBLIC_REPAIR_STAGES:
        repair_stage = None
    weighted_score = _safe_public_number(
        decision.get("weighted_overall_score"),
        minimum=0,
        maximum=100,
    )

    return {
        "phase": phase,
        "route": decision["route"],
        "repair_stage": repair_stage,
        "targets": {
            "sub_question_ids": target_sub_questions,
            "report_segment_ids": target_segments,
        },
        "target_sub_question_ids": target_sub_questions,
        "target_report_segment_ids": target_segments,
        "reason_code": _safe_public_code(decision.get("reason_code")),
        "reason": _safe_public_text(
            decision.get("reason"),
            "The deterministic controller selected this route.",
        ),
        "budgets": budget,
        "weighted_overall_score": weighted_score,
    }


def _capture_node_output(state: dict[str, Any], output: Any) -> None:
    if not isinstance(output, Mapping):
        return
    for key in _TRACKED_STATE_KEYS:
        if key in output:
            state[key] = output[key]


def _report_metadata(value: Any) -> dict[str, Any]:
    report = _public_mapping(value)
    if report is None:
        return {"present": False, "section_count": 0, "source_count": 0}
    sections = report.get("sections")
    sources = report.get("sources")
    return {
        "present": True,
        "section_count": len(sections) if isinstance(sections, list) else 0,
        "source_count": len(sources) if isinstance(sources, list) else 0,
    }


def _has_post_synthesis_acceptance(state: Mapping[str, Any]) -> bool:
    try:
        evaluation_run = PostSynthesisEvaluationRun.model_validate(
            state.get("post_synthesis_evaluation_run")
        )
        decision = PostSynthesisRoutingDecision.model_validate(
            state.get("post_synthesis_controller_decision")
        )
        candidate = ResearchReport.model_validate(state.get("candidate_report"))
        final_report = ResearchReport.model_validate(state.get("final_report"))
    except (TypeError, ValueError):
        return False
    version = state.get("report_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return False
    candidate_digest = report_digest(candidate)
    final_digest = report_digest(final_report)
    return (
        evaluation_run.status == "completed"
        and evaluation_run.evaluation is not None
        and decision.route == "accept"
        and decision.repair_stage == RepairStage.INITIAL
        and evaluation_run.report_digest == candidate_digest
        and evaluation_run.report_version == version
        and decision.report_digest == candidate_digest
        and decision.report_version == version
        and decision.evaluation_digest == evaluation_digest(evaluation_run)
        and final_digest == candidate_digest
        and post_synthesis_evaluation_is_acceptable(state)
    )


def _candidate_diagnostics(state: Mapping[str, Any]) -> dict[str, Any]:
    current_decision = _public_mapping(state.get("controller_decision")) or {}
    active_phase = current_decision.get("evaluation_phase")
    if active_phase not in {"pre_synthesis", "post_synthesis"}:
        has_post_decision = (
            state.get("post_synthesis_controller_decision") is not None
            or state.get("post_controller_decision") is not None
        )
        active_phase = "post_synthesis" if has_post_decision else "pre_synthesis"

    evaluator = _evaluation_event_payload(state, phase=active_phase)
    route = _route_event_payload(state, phase=active_phase)

    diagnostics: dict[str, Any] = {
        "terminal_status": state.get("terminal_status") or "incomplete",
        "report_accepted": state.get("report_accepted") is True,
        "candidate_report": _report_metadata(state.get("candidate_report")),
    }
    error_code = _safe_public_code(state.get("workflow_error_code"), limit=100)
    if error_code is not None:
        diagnostics["workflow_error_code"] = error_code
    if evaluator is not None:
        diagnostics["last_evaluation"] = evaluator
    if route is not None:
        diagnostics["last_route"] = route
    return diagnostics


def _publishable_report(state: Mapping[str, Any]) -> ResearchReport | None:
    if (
        state.get("terminal_status") != "completed"
        or state.get("report_accepted") is not True
        or not _has_post_synthesis_acceptance(state)
    ):
        return None
    report = state.get("final_report")
    if report is None:
        return None
    try:
        parsed = report if isinstance(report, ResearchReport) else ResearchReport.model_validate(report)
    except (TypeError, ValueError):
        return None
    if not parsed.sections or not all(section.content.strip() for section in parsed.sections):
        return None

    candidate = state.get("candidate_report")
    if candidate is not None:
        try:
            parsed_candidate = (
                candidate
                if isinstance(candidate, ResearchReport)
                else ResearchReport.model_validate(candidate)
            )
        except (TypeError, ValueError):
            return None
        if parsed_candidate != parsed:
            return None
    return parsed


def _incomplete_result(
    state: Mapping[str, Any],
    run_id: str,
) -> DeepResearchRunResult:
    reason = _safe_public_text(
        state.get("terminal_reason"),
        "The candidate report did not pass the research quality gate.",
    )
    return DeepResearchRunResult(
        run_id=run_id,
        status="incomplete",
        message=reason,
        terminal_reason=reason,
        candidate_diagnostics=_candidate_diagnostics(state),
    )


_RESUMABLE_WORKFLOW_STATUSES = {
    WorkflowRunStatus.running,
    WorkflowRunStatus.interrupted,
    WorkflowRunStatus.failed,
}
_TERMINAL_WORKFLOW_STATUSES = {
    WorkflowRunStatus.completed,
    WorkflowRunStatus.incomplete,
}


def _status_value(status: Any) -> str:
    return status.value if isinstance(status, Enum) else str(status)


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": code, "message": message},
    )


def _runtime_conflict(exc: BaseException) -> HTTPException:
    if isinstance(exc, DeepResearchCheckpointNotFound):
        return _conflict(
            "checkpoint_not_found",
            "This Deep Research run has no restorable checkpoint.",
        )
    if isinstance(exc, DeepResearchGraphVersionMismatch):
        return _conflict(
            "graph_version_mismatch",
            "This checkpoint was created by an incompatible research graph version.",
        )
    if isinstance(exc, DeepResearchCheckpointAlreadyExists):
        return _conflict(
            "checkpoint_already_exists",
            "This run already has a checkpoint and must be resumed.",
        )
    if isinstance(exc, DeepResearchRunAlreadyActive):
        return _conflict(
            "run_already_active",
            "This Deep Research run is already active.",
        )
    if isinstance(exc, DeepResearchRunLockUnavailable):
        return HTTPException(
            status_code=503,
            detail={
                "code": "run_lock_unavailable",
                "message": "The Deep Research run lock service is unavailable.",
            },
        )
    if isinstance(exc, (DeepResearchRuntimeNotInitialized, DeepResearchRuntimeError)):
        return HTTPException(
            status_code=503,
            detail={
                "code": "checkpoint_runtime_unavailable",
                "message": "The Deep Research checkpoint runtime is unavailable.",
            },
        )
    return HTTPException(
        status_code=500,
        detail={
            "code": "deep_research_internal_error",
            "message": "Deep Research could not be completed.",
        },
    )


async def _require_owned_workspace(workspace_id: str, guest_id: str) -> None:
    if await get_owned_workspace(workspace_id=workspace_id, guest_id=guest_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")


async def _require_owned_deep_research_run(
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
) -> WorkflowRun:
    """Authorize in the application database before touching checkpoints."""

    run = await get_owned_workflow_run(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        run_type=WorkflowRunType.deep_research,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


async def _create_durable_deep_research_run(
    *,
    run_id: str,
    req: DeepResearchRequest,
    guest_id: str,
) -> None:
    try:
        persisted_run_id = await create_workflow_run(
            run_id=run_id,
            workspace_id=req.workspace_id,
            guest_id=guest_id,
            run_type=WorkflowRunType.deep_research,
            input_payload=req.model_dump(mode="json"),
        )
    except Exception as exc:
        logger.error(
            "dr_workflow_run_create_failed",
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "workflow_persistence_unavailable",
                "message": "Research persistence could not be initialized.",
            },
        ) from exc
    if persisted_run_id != run_id:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "workflow_identity_mismatch",
                "message": "Research persistence returned an invalid run identity.",
            },
        )


def _snapshot_checkpoint_id(snapshot: Any) -> str | None:
    config = getattr(snapshot, "config", None)
    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    return _safe_public_code(configurable.get("checkpoint_id"), limit=255)


def _snapshot_values(snapshot: Any) -> dict[str, Any]:
    values = getattr(snapshot, "values", None)
    if not isinstance(values, Mapping):
        raise RuntimeError("Deep Research checkpoint state is unavailable.")
    return dict(values)


async def _owned_artifacts(
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    snapshot: bool,
) -> list[Any]:
    async with AsyncSessionLocal() as db:
        if snapshot:
            return await get_artifact_snapshot(
                db,
                run_id=run_id,
                workspace_id=workspace_id,
                guest_id=guest_id,
            )
        return await list_artifact_versions(
            db,
            run_id=run_id,
            workspace_id=workspace_id,
            guest_id=guest_id,
        )


async def _persisted_terminal_result(
    run: WorkflowRun,
    *,
    guest_id: str,
) -> DeepResearchRunResult:
    """Replay an immutable terminal outcome without consulting the runtime/LLM."""

    run_id = str(run.id)
    status = _status_value(run.status)
    public_refs = dict(run.artifacts or {})
    artifacts = await _owned_artifacts(
        run_id=run_id,
        workspace_id=str(run.workspace_id),
        guest_id=guest_id,
        snapshot=False,
    )
    by_id = {str(item.id): item for item in artifacts}
    terminal = by_id.get(str(public_refs.get("terminal_artifact_id") or ""))
    if terminal is None or terminal.content_hash != public_refs.get("terminal_artifact_hash"):
        raise _conflict(
            "terminal_artifact_invalid",
            "The persisted terminal decision is missing or failed integrity checks.",
        )
    terminal_payload = _public_mapping(terminal.payload) or {}
    if terminal_payload.get("terminal_status") != status:
        raise _conflict(
            "terminal_artifact_invalid",
            "The persisted terminal decision conflicts with the run status.",
        )

    state: dict[str, Any] = {
        "terminal_status": status,
        "terminal_reason": terminal_payload.get("terminal_reason"),
        "workflow_error_code": terminal_payload.get("workflow_error_code"),
        "report_accepted": terminal_payload.get("report_accepted") is True,
        "report_version": terminal_payload.get("report_version"),
    }
    refs = _public_mapping(terminal_payload.get("artifact_refs")) or {}
    for ref_name, state_key, payload_key in (
        ("report_candidate", "candidate_report", "candidate_report"),
        (
            "post_synthesis_evaluation",
            "post_synthesis_evaluation_run",
            "evaluation_run",
        ),
        (
            "post_synthesis_controller",
            "post_synthesis_controller_decision",
            "decision",
        ),
    ):
        ref = _public_mapping(refs.get(ref_name)) or {}
        artifact = by_id.get(str(ref.get("artifact_id") or ""))
        if artifact is None or artifact.content_hash != ref.get("content_hash"):
            continue
        payload = _public_mapping(artifact.payload) or {}
        if payload_key in payload:
            state[state_key] = payload[payload_key]
    state["final_report"] = state.get("candidate_report") if status == "completed" else None

    if status == "completed":
        report = _publishable_report(state)
        if report is None:
            raise _conflict(
                "terminal_artifact_invalid",
                "The persisted report no longer satisfies its accepted quality binding.",
            )
        return _report_to_result(report, run_id)
    if status == "incomplete":
        return _incomplete_result(state, run_id)
    raise _conflict(
        "run_not_terminal",
        "This Deep Research run does not have a replayable terminal outcome.",
    )


async def _verify_graph_terminal_persisted(
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    state: Mapping[str, Any],
) -> WorkflowRun:
    """Fail closed unless the terminal graph node committed before publication."""

    report = _publishable_report(state)
    expected = (
        WorkflowRunStatus.completed
        if report is not None
        else WorkflowRunStatus.incomplete
    )
    if report is None and (
        state.get("terminal_status") != "incomplete"
        or state.get("report_accepted") is True
    ):
        raise RuntimeError("Graph ended without a valid terminal quality decision.")
    run = await _require_owned_deep_research_run(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
    )
    if run.status != expected or run.completed_at is None:
        raise RuntimeError("Terminal workflow persistence did not commit.")
    public_artifacts = dict(run.artifacts or {})
    if (
        public_artifacts.get("terminal_status") != expected.value
        or public_artifacts.get("report_accepted") is not (report is not None)
        or not public_artifacts.get("terminal_artifact_id")
        or not public_artifacts.get("terminal_artifact_hash")
    ):
        raise RuntimeError("Terminal artifact binding did not commit.")
    return run


async def _record_execution_stop(
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    status: WorkflowRunStatus,
    code: str,
) -> None:
    await stop_owned_deep_research_run(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        status=status,
        error={"code": code, "message": "Deep Research execution stopped."},
        artifacts={
            "terminal_status": status.value,
            "report_accepted": False,
        },
    )


async def _committed_terminal_result_or_none(
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
) -> DeepResearchRunResult | None:
    run = await get_owned_workflow_run(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        run_type=WorkflowRunType.deep_research,
    )
    if run is None or run.status not in _TERMINAL_WORKFLOW_STATUSES:
        return None
    return await _persisted_terminal_result(run, guest_id=guest_id)


def _terminal_payload(result: DeepResearchRunResult) -> dict[str, Any]:
    if result.status == "completed":
        return {
            "status": "completed",
            "run_id": result.run_id,
            "data": {
                "section_updates": [
                    item.model_dump(mode="json") for item in result.section_updates
                ],
                "discovered_sources": [],
                "saved_source_ids": [],
                "selected_source_ids": result.selected_source_ids,
                "unresolved_questions": result.unresolved_questions,
                "follow_up_items": [
                    item.model_dump(mode="json") for item in result.follow_up_items
                ],
                "summary": result.summary,
                "generated_title": result.generated_title,
                "generated_outline": result.generated_outline,
            },
        }
    return result.model_dump(mode="json", exclude_none=True)


_NODE_RESEARCH_PHASE: dict[str, str] = {
    "plan": "planning",
    "execute": "executing",
    "evaluate": "pre_synthesis_evaluation",
    "controller": "routing",
    "targeted_repair": "targeted_repair",
    "partial_replan": "partial_replan",
    "full_replan": "full_replan",
    "synthesize": "synthesizing",
    "evaluate_report": "post_synthesis_evaluation",
    "post_controller": "routing",
    "revise_report": "report_revision",
    "finalize_complete": "finalizing",
    "finalize_incomplete": "finalizing",
}
_PERSISTED_PRODUCER: dict[str, str] = {
    "persist_initial_plan": "plan",
    "persist_sub_reports": "execute",
    "persist_pre_evaluation": "evaluate",
    "persist_pre_controller": "controller",
    "persist_repair_plan": "repair",
    "persist_synthesis_candidate": "synthesize",
    "persist_post_evaluation": "evaluate_report",
    "persist_post_controller": "post_controller",
    "persist_revised_candidate": "revise_report",
    "persist_terminal": "terminal",
}


def _resume_event_payload(
    *,
    allowed: bool,
    checkpoint_id: str | None,
    reason_code: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "allowed": allowed,
        "checkpoint_id": checkpoint_id,
        "reason_code": reason_code,
        "reason": reason,
    }


def _checkpoint_created_at(snapshot: Any) -> str:
    created_at = getattr(snapshot, "created_at", None)
    if isinstance(created_at, str) and created_at.strip():
        return created_at
    if isinstance(created_at, datetime):
        return utc_iso(created_at)
    return utc_iso(None)


def _coerce_questions(value: Any) -> list[SubQuestion]:
    if not isinstance(value, list):
        return []
    questions: list[SubQuestion] = []
    for item in value:
        try:
            questions.append(SubQuestion.model_validate(item))
        except (TypeError, ValueError):
            continue
    return questions


def _coerce_reports(value: Any) -> list[SubReport]:
    if not isinstance(value, list):
        return []
    reports: list[SubReport] = []
    for item in value:
        try:
            reports.append(SubReport.model_validate(item))
        except (TypeError, ValueError):
            continue
    return reports


class _ResearchEventBridge:
    """Translate LangGraph boundaries into persisted Console v1 events."""

    def __init__(
        self,
        *,
        writer: DurableRunEventWriter,
        evaluator_model: str,
    ) -> None:
        self.writer = writer
        self.evaluator_model = evaluator_model or "configured-model"
        self.phase_started_at: dict[str, float] = {}
        self.pending_outputs: dict[str, Mapping[str, Any]] = {}
        self.phase_artifact_ids: dict[str, list[str]] = {}
        self.artifacts_by_id: dict[str, Any] = {}
        self.latest_artifact_by_kind: dict[str, Any] = {}
        self.question_attempts: dict[str, int] = {}
        self.active_question_ids: set[str] = set()
        self.pending_question_progress: dict[str, Mapping[str, Any]] = {}
        self.pending_segments: dict[str, Mapping[str, Any]] = {}
        self.seen_checkpoints: set[str] = set()

    async def _emit(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        boundary: str,
        checkpoint_id: str | None = None,
    ) -> str:
        event = await self.writer.append(
            event_type,
            payload,
            boundary=boundary,
            checkpoint_id=checkpoint_id,
        )
        return sse_run_event(event)

    async def run_started(
        self,
        *,
        boundary: str,
        resume_checkpoint_id: str | None = None,
    ) -> str:
        resuming = bool(resume_checkpoint_id)
        return await self._emit(
            "run_started",
            {
                "workspace_id": self.writer.workspace_id,
                "topic": str(self.writer.state.get("topic") or "Research run"),
                "graph_version": DEEP_RESEARCH_GRAPH_VERSION,
                "status": "running",
                "budget": budget_payload(self.writer.state.get("budget_snapshot")),
                "resume": _resume_event_payload(
                    allowed=resuming,
                    checkpoint_id=resume_checkpoint_id,
                    reason_code=(
                        "checkpoint_restorable" if resuming else "checkpoint_pending"
                    ),
                    reason=(
                        "Execution is continuing from this durable checkpoint."
                        if resuming
                        else "The first durable checkpoint has not been saved yet."
                    ),
                ),
            },
            boundary=boundary,
        )

    async def checkpoint_saved(self, snapshot: Any, *, boundary: str) -> str | None:
        checkpoint_id = _snapshot_checkpoint_id(snapshot)
        if not checkpoint_id or checkpoint_id in self.seen_checkpoints:
            return None
        self.seen_checkpoints.add(checkpoint_id)
        self.writer.checkpoint_id = checkpoint_id
        next_nodes = [str(node) for node in (getattr(snapshot, "next", ()) or ())]
        restorable = bool(next_nodes)
        return await self._emit(
            "checkpoint_saved",
            {
                "checkpoint": {
                    "checkpoint_id": checkpoint_id,
                    "graph_version": DEEP_RESEARCH_GRAPH_VERSION,
                    "restorable": restorable,
                    "saved_at": _checkpoint_created_at(snapshot),
                    "next_nodes": next_nodes,
                },
                "resume": _resume_event_payload(
                    allowed=restorable,
                    checkpoint_id=checkpoint_id,
                    reason_code=(
                        "checkpoint_restorable" if restorable else "run_terminal"
                    ),
                    reason=(
                        "A server checkpoint can resume this run."
                        if restorable
                        else "The run has no remaining executable nodes."
                    ),
                ),
            },
            boundary=f"{boundary}:{checkpoint_id}",
            checkpoint_id=checkpoint_id,
        )

    async def _load_artifact(self, artifact_id: str) -> Any | None:
        async with AsyncSessionLocal() as db:
            return await db.scalar(
                select(DeepResearchArtifactVersion).where(
                    DeepResearchArtifactVersion.id == artifact_id,
                    DeepResearchArtifactVersion.run_id == self.writer.run_id,
                    DeepResearchArtifactVersion.workspace_id == self.writer.workspace_id,
                    DeepResearchArtifactVersion.guest_id == self.writer.guest_id,
                )
            )

    async def artifact_created(self, data: Mapping[str, Any], node: str) -> str | None:
        artifact_id = data.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            return None
        artifact = await self._load_artifact(artifact_id)
        if artifact is None:
            raise RuntimeError("Committed Deep Research artifact could not be reloaded.")
        self.artifacts_by_id[artifact_id] = artifact
        kind = artifact.artifact_kind
        kind_value = kind.value if hasattr(kind, "value") else str(kind)
        self.latest_artifact_by_kind[kind_value] = artifact
        producer = _PERSISTED_PRODUCER.get(node, node)
        self.phase_artifact_ids.setdefault(producer, []).append(artifact_id)
        return await self._emit(
            "artifact_version_created",
            {"artifact": artifact_ref(artifact)},
            boundary=artifact_id,
            checkpoint_id=artifact.source_checkpoint_id,
        )

    def _round_ids(self, phase: str) -> tuple[str, str, dict[str, Any]]:
        reports = _coerce_reports(self.writer.state.get("sub_reports"))
        if phase == "pre_synthesis":
            subject = {
                "kind": "corpus",
                "digest": corpus_digest(reports),
                "version": int(self.writer.state.get("corpus_version") or 0),
            }
        else:
            raw_report = self.writer.state.get("candidate_report")
            try:
                report = ResearchReport.model_validate(raw_report)
                digest = report_digest(report)
            except (TypeError, ValueError):
                digest = "0" * 64
            subject = {
                "kind": "report",
                "digest": digest,
                "version": int(self.writer.state.get("report_version") or 0),
            }
        identity = (
            f"{phase}:{subject['digest']}:{subject['version']}:"
            f"{len(self.writer.state.get('routing_history', []) or [])}:"
            f"{len(self.writer.state.get('post_routing_history', []) or [])}"
        )
        round_id = self.writer.event_id("evaluation_started", f"round:{identity}")
        evaluation_id = self.writer.event_id(
            "evaluation_completed", f"evaluation:{identity}"
        )
        return round_id, evaluation_id, subject

    async def evaluation_started(self, phase: str, *, boundary: str) -> str:
        round_id, evaluation_id, subject = self._round_ids(phase)
        return await self._emit(
            "evaluation_started",
            {
                "evaluation_id": evaluation_id,
                "round_id": round_id,
                "phase": phase,
                "subject": subject,
                "evaluator_model": self.evaluator_model,
            },
            boundary=boundary,
        )

    async def evaluation_completed(self, phase: str, *, boundary: str) -> str | None:
        key = (
            "pre_synthesis_evaluation_run"
            if phase == "pre_synthesis"
            else "post_synthesis_evaluation_run"
        )
        model_type = (
            PreSynthesisEvaluationRun
            if phase == "pre_synthesis"
            else PostSynthesisEvaluationRun
        )
        try:
            run = model_type.model_validate(self.writer.state.get(key))
        except (TypeError, ValueError):
            return None
        round_id, evaluation_id, subject = self._round_ids(phase)
        evaluation = run.evaluation
        scores = (
            evaluation.scores.model_dump(mode="json")
            if evaluation is not None
            else {}
        )
        issues: list[dict[str, Any]] = []
        if evaluation is not None:
            for issue in evaluation.issues:
                issues.append(
                    {
                        "id": issue.id,
                        "category": issue.category,
                        "severity": issue.severity,
                        "suggested_repair_stage": getattr(
                            issue, "suggested_repair_stage", None
                        ),
                        "affected_sub_question_ids": list(
                            getattr(issue, "affected_sub_question_ids", [])
                        ),
                        "claim_ids": list(getattr(issue, "claim_ids", [])),
                        "segment_ids": list(getattr(issue, "segment_ids", [])),
                    }
                )
        artifact_kind = (
            "pre_synthesis_evaluation"
            if phase == "pre_synthesis"
            else "post_synthesis_evaluation"
        )
        artifact = self.latest_artifact_by_kind.get(artifact_kind)
        return await self._emit(
            "evaluation_completed",
            {
                "evaluation_id": evaluation_id,
                "round_id": round_id,
                "phase": phase,
                "status": run.status,
                "subject": subject,
                "evaluator_model": run.evaluator_model,
                "attempts": run.attempts,
                "duration_ms": run.duration_ms,
                "scores": scores,
                "issues": issues,
                "summary": evaluation.summary if evaluation is not None else None,
                "error_code": run.error_code,
                "artifact_version_id": str(artifact.id) if artifact is not None else None,
            },
            boundary=boundary,
        )

    async def route_selected(self, phase: str, *, boundary: str) -> str | None:
        key = (
            "controller_decision"
            if phase == "pre_synthesis"
            else "post_synthesis_controller_decision"
        )
        model_type = RoutingDecision if phase == "pre_synthesis" else PostSynthesisRoutingDecision
        try:
            decision = model_type.model_validate(self.writer.state.get(key))
        except (TypeError, ValueError):
            return None
        round_id, evaluation_id, _subject = self._round_ids(phase)
        decision_id = self.writer.event_id(
            "route_selected",
            f"decision:{phase}:{decision.fingerprint}:{decision.route}",
        )
        artifact = self.latest_artifact_by_kind.get("controller_transition")
        repair_stage = decision.repair_stage
        return await self._emit(
            "route_selected",
            {
                "decision_id": decision_id,
                "round_id": round_id,
                "evaluation_id": evaluation_id,
                "phase": phase,
                "route": decision.route,
                "repair_stage": (
                    repair_stage.value if hasattr(repair_stage, "value") else str(repair_stage)
                ),
                "weighted_overall_score": decision.weighted_overall_score,
                "reason_code": decision.reason_code,
                "reason": decision.reason,
                "target_sub_question_ids": list(decision.affected_sub_question_ids),
                "target_report_segment_ids": list(
                    getattr(decision, "target_report_segment_ids", [])
                ),
                "budget": budget_payload(decision.budget),
                "artifact_version_id": str(artifact.id) if artifact is not None else None,
            },
            boundary=boundary,
        )

    def _question_origin(self) -> str:
        plan_version = self.writer.state.get("plan_version", 1)
        if not isinstance(plan_version, int) or plan_version <= 1:
            return "initial"
        stage = self.writer.state.get("repair_stage")
        stage_value = stage.value if hasattr(stage, "value") else str(stage or "")
        if stage_value == "targeted_repair":
            return "targeted_repair"
        if stage_value == "partial_replan":
            return "partial_replan"
        return "full_replan"

    async def plan_updated(self, *, boundary: str) -> list[str]:
        events: list[str] = []
        questions = _coerce_questions(self.writer.state.get("sub_questions"))
        current_ids = {question.id for question in questions}
        removed = self.active_question_ids - current_ids
        for removed_id in sorted(removed):
            events.append(
                await self._emit(
                    "subquestion_progressed",
                    {
                        "sub_question_id": removed_id,
                        "status": "superseded",
                        "attempt": self.question_attempts.get(removed_id, 0),
                        "confidence": None,
                        "duration_ms": None,
                        "error_code": None,
                        "error_message": None,
                        "sub_report_artifact_version_id": None,
                    },
                    boundary=f"{boundary}:superseded:{removed_id}",
                )
            )
        reports = {
            report.sub_question_id: report
            for report in _coerce_reports(self.writer.state.get("sub_reports"))
        }
        plan_version = int(self.writer.state.get("plan_version") or 1)
        origin = self._question_origin()
        for order, question in enumerate(questions):
            report = reports.get(question.id)
            artifact = next(
                (
                    item
                    for item in self.artifacts_by_id.values()
                    if (
                        (item.artifact_kind.value if hasattr(item.artifact_kind, "value") else str(item.artifact_kind))
                        == "sub_report"
                        and item.logical_artifact_id == f"sub-question:{question.id}"
                    )
                ),
                None,
            )
            events.append(
                await self._emit(
                    "subquestion_upserted",
                    {
                        "sub_question": {
                            "id": question.id,
                            "question": question.question,
                            "priority": question.priority,
                            "order": order,
                            "plan_version": plan_version,
                            "origin": origin,
                            "status": "completed" if report is not None else "pending",
                            "attempt": self.question_attempts.get(question.id, 0),
                            "confidence": report.confidence if report is not None else None,
                            "duration_ms": None,
                            "error_code": None,
                            "error_message": None,
                            "sub_report_artifact_version_id": (
                                str(artifact.id) if artifact is not None else None
                            ),
                        }
                    },
                    boundary=f"{boundary}:question:{question.id}:{plan_version}",
                )
            )
        self.active_question_ids = current_ids
        return events

    async def execution_completed(self, *, boundary: str) -> list[str]:
        events: list[str] = []
        failures = {
            item.get("sub_question_id"): item
            for item in self.writer.state.get("failed_queries", [])
            if isinstance(item, Mapping)
        }
        reports = {
            report.sub_question_id: report
            for report in _coerce_reports(self.writer.state.get("sub_reports"))
        }
        for question in _coerce_questions(self.writer.state.get("sub_questions")):
            report = reports.get(question.id)
            failure = failures.get(question.id)
            if report is None and failure is None:
                continue
            artifact = next(
                (
                    item
                    for item in self.artifacts_by_id.values()
                    if (
                        (item.artifact_kind.value if hasattr(item.artifact_kind, "value") else str(item.artifact_kind))
                        == "sub_report"
                        and item.logical_artifact_id == f"sub-question:{question.id}"
                    )
                ),
                None,
            )
            progress = self.pending_question_progress.get(question.id, {})
            events.append(
                await self._emit(
                    "subquestion_progressed",
                    {
                        "sub_question_id": question.id,
                        "status": "failed" if failure is not None else "completed",
                        "attempt": max(1, self.question_attempts.get(question.id, 1)),
                        "confidence": report.confidence if report is not None else 0,
                        "duration_ms": progress.get("duration_ms"),
                        "error_code": failure.get("error_code") if failure else None,
                        "error_message": failure.get("reason") if failure else None,
                        "sub_report_artifact_version_id": (
                            str(artifact.id) if artifact is not None else None
                        ),
                    },
                    boundary=f"{boundary}:question:{question.id}:attempt:{max(1, self.question_attempts.get(question.id, 1))}",
                )
            )
        self.pending_question_progress.clear()
        return events

    async def _phase_started(self, node: str, *, boundary: str) -> list[str]:
        phase = _NODE_RESEARCH_PHASE[node]
        self.phase_started_at[node] = time.monotonic()
        evaluation_phase = (
            "pre_synthesis"
            if node == "evaluate"
            else "post_synthesis" if node in {"evaluate_report", "revise_report"} else None
        )
        round_id = None
        if evaluation_phase is not None:
            round_id = self._round_ids(evaluation_phase)[0]
        decision = self.writer.state.get("controller_decision")
        target_questions = list(getattr(decision, "affected_sub_question_ids", []) or [])
        target_segments = list(self.writer.state.get("target_report_segment_ids", []) or [])
        output = [
            await self._emit(
                "phase_started",
                {
                    "phase": phase,
                    "node": node,
                    "label": _NODE_STAGE_MAP.get(node, (phase, phase))[1],
                    "round_id": round_id,
                    "evaluation_phase": evaluation_phase,
                    "target_sub_question_ids": target_questions,
                    "target_report_segment_ids": target_segments,
                    "output_artifact_version_ids": [],
                    "duration_ms": None,
                },
                boundary=boundary,
            )
        ]
        if node == "evaluate":
            output.append(await self.evaluation_started("pre_synthesis", boundary=f"{boundary}:evaluation"))
        elif node == "evaluate_report":
            output.append(await self.evaluation_started("post_synthesis", boundary=f"{boundary}:evaluation"))
        return output

    async def _phase_completed(self, producer: str, *, boundary: str) -> str | None:
        if producer not in _NODE_RESEARCH_PHASE:
            return None
        phase = _NODE_RESEARCH_PHASE[producer]
        started = self.phase_started_at.pop(producer, None)
        duration_ms = (
            max(0, round((time.monotonic() - started) * 1000))
            if started is not None
            else None
        )
        evaluation_phase = (
            "pre_synthesis"
            if producer == "evaluate"
            else "post_synthesis" if producer in {"evaluate_report", "revise_report"} else None
        )
        round_id = self._round_ids(evaluation_phase)[0] if evaluation_phase else None
        return await self._emit(
            "phase_completed",
            {
                "phase": phase,
                "node": producer,
                "label": _NODE_STAGE_MAP.get(producer, (phase, phase))[1],
                "round_id": round_id,
                "evaluation_phase": evaluation_phase,
                "target_sub_question_ids": list(
                    self.writer.state.get("execution_target_ids") or []
                ),
                "target_report_segment_ids": list(
                    self.writer.state.get("target_report_segment_ids") or []
                ),
                "output_artifact_version_ids": self.phase_artifact_ids.pop(producer, []),
                "duration_ms": duration_ms,
                "status": "completed",
            },
            boundary=boundary,
        )

    async def _flush_persisted_boundary(self, producer: str, *, boundary: str) -> list[str]:
        events: list[str] = []
        if producer in {"plan", "repair"}:
            events.extend(await self.plan_updated(boundary=boundary))
        elif producer == "execute":
            events.extend(await self.execution_completed(boundary=boundary))
        elif producer == "evaluate":
            event = await self.evaluation_completed("pre_synthesis", boundary=boundary)
            if event:
                events.append(event)
        elif producer == "controller":
            event = await self.route_selected("pre_synthesis", boundary=boundary)
            if event:
                events.append(event)
        elif producer == "evaluate_report":
            event = await self.evaluation_completed("post_synthesis", boundary=boundary)
            if event:
                events.append(event)
        elif producer == "post_controller":
            event = await self.route_selected("post_synthesis", boundary=boundary)
            if event:
                events.append(event)
        elif producer in {"synthesize", "revise_report"}:
            candidate = self.writer.state.get("candidate_report")
            if candidate is not None:
                try:
                    report = ResearchReport.model_validate(candidate)
                except (TypeError, ValueError):
                    report = None
                if report is not None:
                    version = int(self.writer.state.get("report_version") or 1)
                    artifact = self.latest_artifact_by_kind.get("report_candidate")
                    for segment in build_report_segments(report):
                        if segment.component != "section":
                            continue
                        progress = self.pending_segments.get(segment.id, {})
                        events.append(
                            await self._emit(
                                "synthesis_section_updated",
                                {
                                    "segment": {
                                        "segment_id": segment.id,
                                        "title": segment.heading or segment.id,
                                        "status": "completed",
                                        "report_version": version,
                                        "duration_ms": progress.get("duration_ms"),
                                        "artifact_version_id": (
                                            str(artifact.id) if artifact is not None else None
                                        ),
                                    }
                                },
                                boundary=f"{boundary}:segment:{segment.id}:{version}",
                            )
                        )
            self.pending_segments.clear()
        phase_producer = producer if producer != "repair" else str(
            self.writer.state.get("repair_stage") or "full_replan"
        )
        if hasattr(self.writer.state.get("repair_stage"), "value"):
            phase_producer = self.writer.state["repair_stage"].value
        phase_event = await self._phase_completed(phase_producer, boundary=boundary)
        if phase_event:
            events.append(phase_event)
        return events

    async def handle(self, event: Mapping[str, Any], runtime: Any) -> list[str]:
        kind = str(event.get("event") or "")
        name = str(event.get("name") or "")
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        step = metadata.get("langgraph_step", 0)
        node = str(metadata.get("langgraph_node") or name)
        output: list[str] = []

        if kind == "on_chain_start" and name in _NODE_RESEARCH_PHASE and node == name:
            try:
                snapshot = await runtime.aget_state(self.writer.run_id)
            except Exception:
                snapshot = None
            if snapshot is not None:
                checkpoint_event = await self.checkpoint_saved(
                    snapshot, boundary=f"node-start:{name}:{step}"
                )
                if checkpoint_event:
                    output.append(checkpoint_event)
            output.extend(
                await self._phase_started(
                    name,
                    boundary=f"node-start:{name}:{self.writer.checkpoint_id or step}",
                )
            )
            return output

        if kind == "on_custom_event" and name == "artifact_version_created":
            artifact_event = await self.artifact_created(data, node)
            if artifact_event:
                output.append(artifact_event)
            return output

        if kind == "on_custom_event" and name == "execute_progress":
            sub_question_id = data.get("sub_question_id")
            if not isinstance(sub_question_id, str) or not sub_question_id:
                return output
            progress_kind = data.get("event")
            if progress_kind == "sq_start":
                self.question_attempts[sub_question_id] = (
                    self.question_attempts.get(sub_question_id, 0) + 1
                )
                output.append(
                    await self._emit(
                        "subquestion_progressed",
                        {
                            "sub_question_id": sub_question_id,
                            "status": "in_progress",
                            "attempt": self.question_attempts[sub_question_id],
                            "confidence": None,
                            "duration_ms": None,
                            "error_code": None,
                            "error_message": None,
                            "sub_report_artifact_version_id": None,
                        },
                        boundary=(
                            f"execute:{self.writer.checkpoint_id or step}:"
                            f"{sub_question_id}:attempt:{self.question_attempts[sub_question_id]}"
                        ),
                    )
                )
            elif progress_kind == "sq_complete":
                self.pending_question_progress[sub_question_id] = data
            return output

        if kind == "on_custom_event" and name == "synthesize_progress":
            segment_id = data.get("segment_id")
            if isinstance(segment_id, str) and segment_id:
                self.pending_segments[segment_id] = data
                if data.get("status") == "start":
                    report_version = data.get("report_version")
                    if not isinstance(report_version, int) or report_version < 1:
                        report_version = int(self.writer.state.get("report_version") or 0) + 1
                    output.append(
                        await self._emit(
                            "synthesis_section_updated",
                            {
                                "segment": {
                                    "segment_id": segment_id,
                                    "title": str(data.get("section_title") or segment_id),
                                    "status": "writing",
                                    "report_version": report_version,
                                    "duration_ms": None,
                                    "artifact_version_id": None,
                                }
                            },
                            boundary=f"segment-start:{report_version}:{segment_id}",
                        )
                    )
            return output

        if kind == "on_chain_end" and name in _NODE_RESEARCH_PHASE and node == name:
            node_output = data.get("output")
            if isinstance(node_output, Mapping):
                self.writer.update_state(node_output)
                self.pending_outputs[name] = node_output
            return output

        if kind == "on_chain_end" and name in _PERSISTED_PRODUCER and node == name:
            producer = _PERSISTED_PRODUCER[name]
            boundary = f"persisted:{name}:{self.writer.checkpoint_id or step}"
            output.extend(await self._flush_persisted_boundary(producer, boundary=boundary))
            return output

        return output


async def _append_terminal_run_event(
    bridge: _ResearchEventBridge,
    *,
    status: str,
    result: DeepResearchRunResult | None,
    snapshot: Any | None,
    reason_code: str | None,
    reason: str | None,
) -> str:
    if snapshot is not None:
        try:
            bridge.writer.update_state(_snapshot_values(snapshot))
        except RuntimeError:
            pass
    artifacts = await _owned_artifacts(
        run_id=bridge.writer.run_id,
        workspace_id=bridge.writer.workspace_id,
        guest_id=bridge.writer.guest_id,
        snapshot=False,
    )
    for artifact in artifacts:
        bridge.artifacts_by_id[str(artifact.id)] = artifact
        kind = artifact.artifact_kind
        kind_value = kind.value if hasattr(kind, "value") else str(kind)
        bridge.latest_artifact_by_kind[kind_value] = artifact
    candidate = bridge.latest_artifact_by_kind.get("report_candidate")
    terminal_artifact = bridge.latest_artifact_by_kind.get("terminal_decision")
    accepted = status == "completed" and result is not None
    next_nodes = (
        [str(node) for node in (getattr(snapshot, "next", ()) or ())]
        if snapshot is not None
        else []
    )
    checkpoint_id = _snapshot_checkpoint_id(snapshot) if snapshot is not None else None
    resumable = status in {"interrupted", "failed"} and bool(next_nodes) and bool(checkpoint_id)
    flat_result: dict[str, Any] | None = None
    if result is not None:
        legacy = _terminal_payload(result)
        if result.status == "completed":
            raw_data = legacy.get("data")
            flat_result = dict(raw_data) if isinstance(raw_data, Mapping) else None
        else:
            flat_result = result.model_dump(mode="json", exclude_none=True)
    event = await bridge.writer.append(
        "run_finished",
        {
            "status": status,
            "report_accepted": accepted,
            "publishable": accepted,
            "terminal_reason_code": reason_code,
            "terminal_reason": reason,
            "candidate_artifact_version_id": (
                str(candidate.id) if candidate is not None else None
            ),
            "final_artifact_version_id": (
                str(candidate.id) if accepted and candidate is not None else None
            ),
            "deliverable_id": None,
            "result": flat_result,
            "resume": _resume_event_payload(
                allowed=resumable,
                checkpoint_id=checkpoint_id,
                reason_code=(
                    "checkpoint_restorable" if resumable else "run_terminal"
                ),
                reason=(
                    "A server checkpoint can resume this run."
                    if resumable
                    else "This execution attempt has a terminal outcome."
                ),
            ),
        },
        boundary=(
            f"terminal:{terminal_artifact.id if terminal_artifact is not None else status}:"
            f"{checkpoint_id or 'no-checkpoint'}"
        ),
        checkpoint_id=checkpoint_id,
    )
    return sse_run_event(event)


async def _new_run_v1_event_stream(
    *,
    req: DeepResearchRequest,
    run_id: str,
    guest_id: str,
):
    initial_state = _build_initial_state(req)
    writer = DurableRunEventWriter(
        run_id=run_id,
        workspace_id=req.workspace_id,
        guest_id=guest_id,
        initial_state=initial_state,
    )
    bridge = _ResearchEventBridge(writer=writer, evaluator_model="configured-model")
    yield await bridge.run_started(boundary="new-run")

    has_pre_plan = req.pre_plan is not None
    if not has_pre_plan:
        clarifications = _validate_and_clarify(req)
        if clarifications:
            await _record_execution_stop(
                run_id=run_id,
                workspace_id=req.workspace_id,
                guest_id=guest_id,
                status=WorkflowRunStatus.failed,
                code="needs_clarification",
            )
            yield await _append_terminal_run_event(
                bridge,
                status="failed",
                result=None,
                snapshot=None,
                reason_code="needs_clarification",
                reason="The research request needs clarification before execution.",
            )
            return

    try:
        llm = await _resolve_llm(guest_id)
    except Exception as exc:
        logger.error("dr_stream_llm_resolve_failed", error_type=type(exc).__name__)
        await _record_execution_stop(
            run_id=run_id,
            workspace_id=req.workspace_id,
            guest_id=guest_id,
            status=WorkflowRunStatus.failed,
            code="llm_initialization_failed",
        )
        yield await _append_terminal_run_event(
            bridge,
            status="failed",
            result=None,
            snapshot=None,
            reason_code="llm_initialization_failed",
            reason="The configured evaluator model could not be initialized.",
        )
        return

    bridge.evaluator_model = llm.resolved.model
    if not has_pre_plan:
        llm_clarifications = await _llm_validate_topic(req, llm)
        if llm_clarifications:
            await _record_execution_stop(
                run_id=run_id,
                workspace_id=req.workspace_id,
                guest_id=guest_id,
                status=WorkflowRunStatus.failed,
                code="needs_clarification",
            )
            yield await _append_terminal_run_event(
                bridge,
                status="failed",
                result=None,
                snapshot=None,
                reason_code="needs_clarification",
                reason="The evaluator found unresolved ambiguity in the research request.",
            )
            return

    graph_context = _build_graph_context(
        run_id=run_id,
        workspace_id=req.workspace_id,
        guest_id=guest_id,
        llm=llm,
        artifact_recorder=PostgresArtifactRecorder(),
        artifact_persistence_required=True,
    )
    trace = create_trace(
        name="deep_research",
        workspace_id=req.workspace_id,
        guest_id=guest_id,
        run_id=run_id,
    )
    callback_handler = get_langfuse_callback_handler(trace)
    callbacks = [callback_handler] if callback_handler else None
    runtime = get_deep_research_runtime()
    snapshot: Any | None = None

    try:
        async for event in _stream_runtime_with_lock(
            runtime=runtime,
            input_state=initial_state,
            run_id=run_id,
            context=graph_context,
            callbacks=callbacks,
        ):
            if (
                event.get("event") == "on_chain_start"
                and event.get("name") in _NODE_STAGE_MAP
            ):
                await update_workflow_stage(
                    run_id,
                    stage=_NODE_STAGE_MAP[event["name"]][0],
                )
            for payload in await bridge.handle(event, runtime):
                yield payload

        snapshot = await runtime.aget_state(run_id)
        writer.update_state(_snapshot_values(snapshot))
        checkpoint_event = await bridge.checkpoint_saved(snapshot, boundary="graph-end")
        if checkpoint_event:
            yield checkpoint_event
        if tuple(getattr(snapshot, "next", ()) or ()):
            await _record_execution_stop(
                run_id=run_id,
                workspace_id=req.workspace_id,
                guest_id=guest_id,
                status=WorkflowRunStatus.interrupted,
                code="execution_interrupted",
            )
            yield await _append_terminal_run_event(
                bridge,
                status="interrupted",
                result=None,
                snapshot=snapshot,
                reason_code="execution_interrupted",
                reason="Research paused at a durable checkpoint.",
            )
            return
        final_state = _snapshot_values(snapshot)
        await _verify_graph_terminal_persisted(
            run_id=run_id,
            workspace_id=req.workspace_id,
            guest_id=guest_id,
            state=final_state,
        )
        report = _publishable_report(final_state)
        if report is not None:
            result = _report_to_result(report, run_id)
            yield await _append_terminal_run_event(
                bridge,
                status="completed",
                result=result,
                snapshot=snapshot,
                reason_code=None,
                reason=None,
            )
        else:
            incomplete = _incomplete_result(final_state, run_id)
            yield await _append_terminal_run_event(
                bridge,
                status="incomplete",
                result=incomplete,
                snapshot=snapshot,
                reason_code=_safe_public_code(final_state.get("workflow_error_code")),
                reason=incomplete.terminal_reason,
            )
    except asyncio.CancelledError:
        try:
            snapshot = await runtime.aget_state(run_id)
        except Exception:
            snapshot = None
        await asyncio.shield(
            _record_execution_stop(
                run_id=run_id,
                workspace_id=req.workspace_id,
                guest_id=guest_id,
                status=WorkflowRunStatus.interrupted,
                code="execution_interrupted",
            )
        )
        await asyncio.shield(
            _append_terminal_run_event(
                bridge,
                status="interrupted",
                result=None,
                snapshot=snapshot,
                reason_code="execution_interrupted",
                reason="The client disconnected; the latest durable checkpoint was retained.",
            )
        )
        raise
    except Exception as exc:
        logger.error("dr_v1_stream_failed", error_type=type(exc).__name__)
        try:
            snapshot = await runtime.aget_state(run_id)
        except Exception:
            snapshot = None
        await _record_execution_stop(
            run_id=run_id,
            workspace_id=req.workspace_id,
            guest_id=guest_id,
            status=WorkflowRunStatus.failed,
            code="execution_failed",
        )
        yield await _append_terminal_run_event(
            bridge,
            status="failed",
            result=None,
            snapshot=snapshot,
            reason_code="execution_failed",
            reason="Deep Research execution failed; inspect the last durable checkpoint.",
        )

async def _stream_runtime_with_lock(
    *,
    runtime: Any,
    input_state: Mapping[str, Any] | None,
    run_id: str,
    context: DeepResearchContext,
    callbacks: Any = None,
    on_complete: Any = None,
):
    async with deep_research_run_lock(run_id):
        async for event in runtime.astream_events(
            input_state,
            run_id,
            context,
            callbacks=callbacks,
        ):
            yield event
        snapshot = await runtime.aget_state(run_id)
        if on_complete is not None:
            await on_complete(snapshot)


# ── Generate plan endpoint ────────────────────────────────────────────────────

@router.post("/generate-plan", response_model=GeneratePlanResponse)
async def generate_plan(
    req: GeneratePlanRequest,
    guest_id: str = Depends(require_guest_id),
):
    topic = req.topic.strip()
    if not topic:
        raise ValueError("Topic is required")

    t0 = time.monotonic()
    logger.info("generate_plan_start", topic=topic[:80], workspace_id=req.workspace_id)

    try:
        llm_client = await _resolve_llm(guest_id)
    except Exception as exc:
        logger.error("generate_plan_llm_resolve_failed", error_type=type(exc).__name__, elapsed_ms=int((time.monotonic() - t0) * 1000))
        raise

    logger.info("generate_plan_llm_resolved", elapsed_ms=int((time.monotonic() - t0) * 1000))

    has_sources = len(req.workspace_sources) > 0
    user_sources = [s.title for s in req.workspace_sources if s.label != "discarded"]

    # Infer depth from topic complexity
    word_count = len(topic.split())
    recommended_depth = "standard"
    if word_count <= 5:
        recommended_depth = "quick"
    elif word_count >= 20:
        recommended_depth = "deep"

    max_questions = DEPTH_CONFIG.get(recommended_depth, 5)
    min_questions = max(3, max_questions - 2)

    sources_block = ""
    if user_sources:
        sources_block = "User-provided sources:\n" + "\n".join(f"- {s}" for s in user_sources)

    system = PLAN_SYSTEM.format(min_questions=min_questions, max_questions=max_questions)
    user_msg = PLAN_USER.format(
        topic=topic,
        depth=recommended_depth,
        max_questions=max_questions,
        sources_block=sources_block,
    )

    # Credentials stay in a local invocation context, never graph state.
    fake_state: DeepResearchState = {
        "topic": topic,
        "user_sources": user_sources,
        "depth": recommended_depth,
        "sub_questions": [],
        "sub_reports": [],
        "failed_queries": [],
        "replan_count": 0,
        "final_report": None,
    }
    plan_context = _build_graph_context(
        run_id=f"generate-plan:{uuid.uuid4()}",
        workspace_id=req.workspace_id,
        guest_id=guest_id,
        llm=llm_client,
    )

    structured_llm = make_dr_structured_llm(
        fake_state,
        Plan,
        context=plan_context,
        max_tokens=2000,
        temperature=0.3,
    )

    logger.info("generate_plan_llm_invoke_start", model=llm_client.resolved.model, depth=recommended_depth, sources_count=len(user_sources))
    t1 = time.monotonic()

    try:
        plan: Plan = await asyncio.wait_for(
            structured_llm.ainvoke([
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ]),
            timeout=90,
        )
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - t1) * 1000)
        logger.error("generate_plan_llm_timeout", elapsed_ms=elapsed)
        raise ValueError("Plan generation timed out after 90s. Please try again or simplify your topic.")

    elapsed_llm = int((time.monotonic() - t1) * 1000)
    logger.info("generate_plan_llm_invoke_done", elapsed_ms=elapsed_llm, sub_questions=len(plan.sub_questions))

    # Determine sources strategy
    if has_sources:
        sources_strategy = "workspace + web"
    else:
        sources_strategy = "web only"

    # Generate focus note for broad topics
    focus_note = None
    if word_count <= 3:
        focus_note = f"Your topic is broad — I've broken it into {len(plan.sub_questions)} focused sub-questions to cover the key angles."

    total_ms = int((time.monotonic() - t0) * 1000)
    logger.info("generate_plan_done", total_ms=total_ms, sub_questions=len(plan.sub_questions))

    return GeneratePlanResponse(
        sub_questions=[
            SubQuestionPreview(
                id=sq.id,
                question=sq.question,
                rationale=sq.rationale,
                search_queries=sq.search_queries,
                priority=sq.priority,
            )
            for sq in plan.sub_questions
        ],
        overall_approach=plan.overall_approach,
        recommended_depth=recommended_depth,
        sources_strategy=sources_strategy,
        focus_note=focus_note,
    )


# ── Main endpoint (batch) ───────────────────────────────────────────────────

@router.post("/run", response_model=DeepResearchRunResult)
async def run_deep_research(
    req: DeepResearchRequest,
    guest_id: str = Depends(require_guest_id),
):
    run_id = str(uuid.uuid4())
    await _require_owned_workspace(req.workspace_id, guest_id)
    await _create_durable_deep_research_run(
        run_id=run_id,
        req=req,
        guest_id=guest_id,
    )

    clarifications = _validate_and_clarify(req)
    if clarifications:
        await _record_execution_stop(
            run_id=run_id,
            workspace_id=req.workspace_id,
            guest_id=guest_id,
            status=WorkflowRunStatus.failed,
            code="needs_clarification",
        )
        return DeepResearchRunResult(
            run_id=run_id, status="needs_clarification",
            clarification_questions=clarifications,
        )

    try:
        llm = await _resolve_llm(guest_id)
    except Exception as exc:
        logger.error("dr_llm_resolve_failed", error_type=type(exc).__name__)
        await _record_execution_stop(
            run_id=run_id,
            workspace_id=req.workspace_id,
            guest_id=guest_id,
            status=WorkflowRunStatus.failed,
            code="llm_initialization_failed",
        )
        return DeepResearchRunResult(
            run_id=run_id, status="failed",
            message="Could not initialize LLM. Check your API key in Settings.",
        )

    # LLM-driven topic validation
    llm_clarifications = await _llm_validate_topic(req, llm)
    if llm_clarifications:
        await _record_execution_stop(
            run_id=run_id,
            workspace_id=req.workspace_id,
            guest_id=guest_id,
            status=WorkflowRunStatus.failed,
            code="needs_clarification",
        )
        return DeepResearchRunResult(
            run_id=run_id, status="needs_clarification",
            clarification_questions=llm_clarifications,
        )

    initial_state = _build_initial_state(req)
    graph_context = _build_graph_context(
        run_id=run_id,
        workspace_id=req.workspace_id,
        guest_id=guest_id,
        llm=llm,
        artifact_recorder=PostgresArtifactRecorder(),
        artifact_persistence_required=True,
    )
    execution_outcome: dict[str, Any] = {}

    async def finish_execution(snapshot: Any) -> None:
        execution_outcome["snapshot"] = snapshot
        if tuple(getattr(snapshot, "next", ()) or ()):
            await _record_execution_stop(
                run_id=run_id,
                workspace_id=req.workspace_id,
                guest_id=guest_id,
                status=WorkflowRunStatus.interrupted,
                code="execution_interrupted",
            )
            execution_outcome["interrupted"] = True
            return
        final_values = _snapshot_values(snapshot)
        await _verify_graph_terminal_persisted(
            run_id=run_id,
            workspace_id=req.workspace_id,
            guest_id=guest_id,
            state=final_values,
        )
        execution_outcome["state"] = final_values

    try:
        runtime = get_deep_research_runtime()
        async for _event in _stream_runtime_with_lock(
            runtime=runtime,
            input_state=initial_state,
            run_id=run_id,
            context=graph_context,
            on_complete=finish_execution,
        ):
            pass
        if execution_outcome.get("interrupted") is True:
            return DeepResearchRunResult(
                run_id=run_id,
                status="interrupted",
                message="Research paused at a durable checkpoint and can be resumed.",
            )
        final_state = execution_outcome["state"]
    except asyncio.CancelledError:
        await asyncio.shield(
            _record_execution_stop(
                run_id=run_id,
                workspace_id=req.workspace_id,
                guest_id=guest_id,
                status=WorkflowRunStatus.interrupted,
                code="execution_interrupted",
            )
        )
        raise
    except Exception as exc:
        logger.error("dr_graph_failed", error_type=type(exc).__name__)
        try:
            await _record_execution_stop(
                run_id=run_id,
                workspace_id=req.workspace_id,
                guest_id=guest_id,
                status=WorkflowRunStatus.failed,
                code="execution_failed",
            )
        except Exception as persist_exc:
            logger.error(
                "dr_failure_persistence_failed",
                error_type=type(persist_exc).__name__,
            )
        try:
            committed_terminal = await _committed_terminal_result_or_none(
                run_id=run_id,
                workspace_id=req.workspace_id,
                guest_id=guest_id,
            )
        except Exception as replay_exc:
            logger.error(
                "dr_terminal_replay_failed",
                error_type=type(replay_exc).__name__,
            )
            committed_terminal = None
        if committed_terminal is not None:
            return committed_terminal
        return DeepResearchRunResult(
            run_id=run_id, status="failed",
            message="Research could not be completed. Please try again.",
        )

    report = _publishable_report(final_state)
    if report is None:
        return _incomplete_result(final_state, run_id)

    return _report_to_result(report, run_id)


# ── Streaming endpoint ──────────────────────────────────────────────────────

@router.post("/run/stream")
@limiter.limit("3/hour")
async def run_deep_research_stream(
    request: Request,
    req: DeepResearchRequest,
    guest_id: str = Depends(require_guest_id),
):
    await _require_owned_workspace(req.workspace_id, guest_id)
    run_id = str(uuid.uuid4())
    await _create_durable_deep_research_run(
        run_id=run_id,
        req=req,
        guest_id=guest_id,
    )

    return StreamingResponse(
        _new_run_v1_event_stream(req=req, run_id=run_id, guest_id=guest_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )

async def _all_owned_run_events(
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
) -> list[Any]:
    events: list[Any] = []
    after_seq = 0
    while True:
        async with AsyncSessionLocal() as db:
            page = await list_run_events(
                db,
                run_id=run_id,
                workspace_id=workspace_id,
                guest_id=guest_id,
                after_seq=after_seq,
                limit=MAX_EVENT_PAGE_SIZE,
            )
        events.extend(page)
        if len(page) < MAX_EVENT_PAGE_SIZE:
            return events
        after_seq = page[-1].seq


def _project_console_events(events: list[Any]) -> dict[str, Any]:
    questions: dict[str, dict[str, Any]] = {}
    question_order: list[str] = []
    rounds: dict[str, dict[str, Any]] = {}
    round_order: list[str] = []
    segments: dict[str, dict[str, Any]] = {}
    segment_order: list[str] = []
    budget = budget_payload(None)
    current_phase: str | None = None
    latest_checkpoint: dict[str, Any] | None = None
    terminal: dict[str, Any] | None = None
    topic = "Research run"
    plan_version = 1
    corpus_version = 0
    report_version: int | None = None

    for row in events:
        payload = dict(row.payload or {})
        plan_version = row.plan_version
        corpus_version = row.corpus_version
        report_version = row.report_version
        if row.type == "run_started":
            topic = str(payload.get("topic") or topic)
            if isinstance(payload.get("budget"), Mapping):
                budget = dict(payload["budget"])
        elif row.type == "phase_started":
            current_phase = payload.get("phase")
        elif row.type == "phase_completed":
            if current_phase == payload.get("phase"):
                current_phase = None
            round_id = payload.get("round_id")
            if isinstance(round_id, str) and round_id in rounds and rounds[round_id].get("repair"):
                repair = dict(rounds[round_id]["repair"])
                repair.update(
                    {
                        "status": payload.get("status") or "completed",
                        "output_artifact_version_ids": list(
                            payload.get("output_artifact_version_ids") or []
                        ),
                        "completed_at": utc_iso(row.emitted_at),
                        "duration_ms": payload.get("duration_ms"),
                    }
                )
                rounds[round_id]["repair"] = repair
        elif row.type == "subquestion_upserted":
            question = payload.get("sub_question")
            if isinstance(question, Mapping) and isinstance(question.get("id"), str):
                question_id = str(question["id"])
                questions[question_id] = dict(question)
                if question_id not in question_order:
                    question_order.append(question_id)
        elif row.type == "subquestion_progressed":
            question_id = payload.get("sub_question_id")
            if isinstance(question_id, str) and question_id in questions:
                questions[question_id].update(
                    {
                        key: payload.get(key)
                        for key in (
                            "status",
                            "attempt",
                            "confidence",
                            "duration_ms",
                            "error_code",
                            "error_message",
                            "sub_report_artifact_version_id",
                        )
                    }
                )
        elif row.type in {"evaluation_started", "evaluation_completed"}:
            round_id = payload.get("round_id")
            phase = payload.get("phase")
            if not isinstance(round_id, str) or not isinstance(phase, str):
                continue
            round_entry = rounds.setdefault(
                round_id,
                {
                    "id": round_id,
                    "cycle": row.cycle,
                    "phase": phase,
                    "plan_version": row.plan_version,
                    "corpus_version": row.corpus_version,
                    "report_version": row.report_version,
                    "evaluation": None,
                    "route": None,
                    "repair": None,
                },
            )
            if round_id not in round_order:
                round_order.append(round_id)
            existing = round_entry.get("evaluation") or {}
            if row.type == "evaluation_started":
                round_entry["evaluation"] = {
                    "evaluation_id": payload.get("evaluation_id"),
                    "round_id": round_id,
                    "phase": phase,
                    "status": "running",
                    "subject": payload.get("subject"),
                    "evaluator_model": payload.get("evaluator_model"),
                    "attempts": 0,
                    "duration_ms": None,
                    "scores": {},
                    "issues": [],
                    "summary": None,
                    "error_code": None,
                    "artifact_version_id": None,
                    "started_at": utc_iso(row.emitted_at),
                    "completed_at": None,
                }
            else:
                round_entry["evaluation"] = {
                    "evaluation_id": payload.get("evaluation_id"),
                    "round_id": round_id,
                    "phase": phase,
                    "status": payload.get("status"),
                    "subject": payload.get("subject"),
                    "evaluator_model": payload.get("evaluator_model"),
                    "attempts": payload.get("attempts", 0),
                    "duration_ms": payload.get("duration_ms"),
                    "scores": dict(payload.get("scores") or {}),
                    "issues": list(payload.get("issues") or []),
                    "summary": payload.get("summary"),
                    "error_code": payload.get("error_code"),
                    "artifact_version_id": payload.get("artifact_version_id"),
                    "started_at": existing.get("started_at") or utc_iso(row.emitted_at),
                    "completed_at": utc_iso(row.emitted_at),
                }
        elif row.type == "route_selected":
            round_id = payload.get("round_id")
            phase = payload.get("phase")
            if not isinstance(round_id, str) or not isinstance(phase, str):
                continue
            round_entry = rounds.setdefault(
                round_id,
                {
                    "id": round_id,
                    "cycle": row.cycle,
                    "phase": phase,
                    "plan_version": row.plan_version,
                    "corpus_version": row.corpus_version,
                    "report_version": row.report_version,
                    "evaluation": None,
                    "route": None,
                    "repair": None,
                },
            )
            if round_id not in round_order:
                round_order.append(round_id)
            round_entry["route"] = {
                "decision_id": payload.get("decision_id"),
                "round_id": round_id,
                "evaluation_id": payload.get("evaluation_id"),
                "phase": phase,
                "route": payload.get("route"),
                "repair_stage": payload.get("repair_stage"),
                "weighted_overall_score": payload.get("weighted_overall_score"),
                "reason_code": payload.get("reason_code"),
                "reason": payload.get("reason"),
                "target_sub_question_ids": list(payload.get("target_sub_question_ids") or []),
                "target_report_segment_ids": list(payload.get("target_report_segment_ids") or []),
                "artifact_version_id": payload.get("artifact_version_id"),
                "selected_at": utc_iso(row.emitted_at),
            }
            if isinstance(payload.get("budget"), Mapping):
                budget = dict(payload["budget"])
        elif row.type == "budget_updated" and isinstance(payload.get("budget"), Mapping):
            budget = dict(payload["budget"])
        elif row.type == "checkpoint_saved":
            checkpoint = payload.get("checkpoint")
            if isinstance(checkpoint, Mapping):
                latest_checkpoint = dict(checkpoint)
        elif row.type == "synthesis_section_updated":
            segment = payload.get("segment")
            if isinstance(segment, Mapping) and isinstance(segment.get("segment_id"), str):
                segment_id = str(segment["segment_id"])
                segments[segment_id] = dict(segment)
                if segment_id not in segment_order:
                    segment_order.append(segment_id)
        elif row.type == "run_finished":
            terminal = {
                "status": payload.get("status"),
                "report_accepted": payload.get("report_accepted") is True,
                "publishable": payload.get("publishable") is True,
                "terminal_reason_code": payload.get("terminal_reason_code"),
                "terminal_reason": payload.get("terminal_reason"),
                "candidate_artifact_version_id": payload.get("candidate_artifact_version_id"),
                "final_artifact_version_id": payload.get("final_artifact_version_id"),
                "deliverable_id": payload.get("deliverable_id"),
                "result": payload.get("result"),
                "finished_at": utc_iso(row.emitted_at),
            }
            current_phase = None

    return {
        "topic": topic,
        "current_phase": current_phase,
        "plan_version": plan_version,
        "corpus_version": corpus_version,
        "report_version": report_version,
        "budget": budget,
        "sub_questions": [questions[item] for item in question_order if item in questions],
        "decision_rounds": [rounds[item] for item in round_order if item in rounds],
        "report_segments": [segments[item] for item in segment_order if item in segments],
        "latest_checkpoint": latest_checkpoint,
        "terminal": terminal,
    }


@router.get("/runs/{run_id}")
async def get_deep_research_run(
    run_id: str,
    workspace_id: str = Query(..., min_length=1, max_length=36),
    guest_id: str = Depends(require_guest_id),
):
    run = await _require_owned_deep_research_run(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
    )
    status = WorkflowRunStatus(_status_value(run.status))
    backend = "unavailable"
    checkpoint_available = False
    checkpoint_id: str | None = None
    next_nodes: list[str] = []
    reason_code = "checkpoint_runtime_unavailable"
    reason = "The checkpoint runtime is unavailable."

    try:
        runtime = get_deep_research_runtime()
        backend = runtime.backend
        snapshot = await runtime.aget_state(run_id)
        checkpoint_available = True
        checkpoint_id = _snapshot_checkpoint_id(snapshot)
        next_nodes = [str(node) for node in (getattr(snapshot, "next", ()) or ())]
        if status in _TERMINAL_WORKFLOW_STATUSES:
            reason_code = "run_terminal"
            reason = "This run already has a terminal outcome."
        elif status in _RESUMABLE_WORKFLOW_STATUSES and next_nodes:
            reason_code = "checkpoint_restorable"
            reason = "A version-compatible checkpoint can be resumed."
        else:
            reason_code = "checkpoint_not_restorable"
            reason = "The checkpoint has no remaining executable nodes."
    except DeepResearchCheckpointNotFound:
        reason_code = "checkpoint_not_found"
        reason = "No checkpoint is available for this run."
    except DeepResearchGraphVersionMismatch:
        reason_code = "graph_version_mismatch"
        reason = "The checkpoint belongs to an incompatible graph version."
    except DeepResearchRuntimeError:
        pass

    resume_allowed = (
        status in _RESUMABLE_WORKFLOW_STATUSES
        and checkpoint_available
        and bool(next_nodes)
    )
    events = await _all_owned_run_events(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
    )
    projection = _project_console_events(events)
    artifacts = await _owned_artifacts(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        snapshot=False,
    )
    if events:
        snapshot_seq = events[-1].seq
        last_event_id = events[-1].event_id
    else:
        snapshot_seq = 0
        last_event_id = None
    raw_input_payload = getattr(run, "input_payload", None)
    input_payload = raw_input_payload if isinstance(raw_input_payload, Mapping) else {}
    input_details = input_payload.get("input") if isinstance(input_payload.get("input"), Mapping) else {}
    topic = projection["topic"]
    if topic == "Research run":
        topic = str(input_details.get("topic") or topic)
    return {
        "schema_version": "deep-research-run.v1",
        "run_id": str(run.id),
        "workspace_id": str(run.workspace_id),
        "topic": topic,
        "status": status.value,
        "graph_version": DEEP_RESEARCH_GRAPH_VERSION,
        "current_phase": projection["current_phase"],
        "snapshot_seq": snapshot_seq,
        "last_event_id": last_event_id,
        "plan_version": projection["plan_version"],
        "corpus_version": projection["corpus_version"],
        "report_version": projection["report_version"],
        "budget": projection["budget"],
        "sub_questions": projection["sub_questions"],
        "decision_rounds": projection["decision_rounds"],
        "artifacts": [artifact_ref(item) for item in artifacts],
        "report_segments": projection["report_segments"],
        "latest_checkpoint": projection["latest_checkpoint"],
        "resume": _resume_event_payload(
            allowed=resume_allowed,
            checkpoint_id=checkpoint_id,
            reason_code=reason_code,
            reason=reason,
        ),
        "terminal": projection["terminal"],
        "created_at": utc_iso(run.created_at),
        "updated_at": utc_iso(run.updated_at),
    }


@router.get(
    "/runs/{run_id}/events",
)
async def list_deep_research_run_event_page(
    run_id: str,
    workspace_id: str = Query(..., min_length=1, max_length=36),
    after_seq: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_EVENT_PAGE_SIZE, ge=1, le=MAX_EVENT_PAGE_SIZE - 1),
    guest_id: str = Depends(require_guest_id),
):
    await _require_owned_deep_research_run(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
    )
    async with AsyncSessionLocal() as db:
        rows = await list_run_events(
            db,
            run_id=run_id,
            workspace_id=workspace_id,
            guest_id=guest_id,
            after_seq=after_seq,
            limit=limit + 1,
        )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_after_seq = page[-1].seq if page else after_seq
    return {
        "events": [serialize_run_event(row) for row in page],
        "next_after_seq": next_after_seq,
        "has_more": has_more,
    }


@router.get(
    "/runs/{run_id}/artifacts",
    response_model=list[DeepResearchArtifactVersionOut],
)
async def list_deep_research_run_artifacts(
    run_id: str,
    workspace_id: str = Query(..., min_length=1, max_length=36),
    snapshot: bool = Query(False),
    guest_id: str = Depends(require_guest_id),
):
    await _require_owned_deep_research_run(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
    )
    artifacts = await _owned_artifacts(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        snapshot=snapshot,
    )
    return [DeepResearchArtifactVersionOut.model_validate(item) for item in artifacts]


async def _terminal_v1_event_stream(
    *,
    run: WorkflowRun,
    guest_id: str,
    result: DeepResearchRunResult,
):
    run_id = str(run.id)
    workspace_id = str(run.workspace_id)
    rows = await _all_owned_run_events(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
    )
    status = _status_value(run.status)
    for row in reversed(rows):
        payload = row.payload if isinstance(row.payload, Mapping) else {}
        if row.type == "run_finished" and payload.get("status") == status:
            yield sse_run_event(row)
            return

    raw_input_payload = getattr(run, "input_payload", None)
    input_payload = raw_input_payload if isinstance(raw_input_payload, Mapping) else {}
    raw_input = input_payload.get("input")
    details = raw_input if isinstance(raw_input, Mapping) else {}
    writer = DurableRunEventWriter(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        initial_state={"topic": str(details.get("topic") or "Research run")},
    )
    bridge = _ResearchEventBridge(writer=writer, evaluator_model="persisted-run")
    yield await _append_terminal_run_event(
        bridge,
        status=status,
        result=result,
        snapshot=None,
        reason_code=None if status == "completed" else "quality_gate_incomplete",
        reason=result.terminal_reason,
    )


async def _resume_v1_event_stream(
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    runtime: Any,
    graph_context: DeepResearchContext,
    llm: LLMClient,
    initial_snapshot: Any,
    release_lock: Any,
):
    state = _snapshot_values(initial_snapshot)
    writer = DurableRunEventWriter(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        initial_state=state,
    )
    checkpoint_id = _snapshot_checkpoint_id(initial_snapshot)
    writer.checkpoint_id = checkpoint_id
    bridge = _ResearchEventBridge(writer=writer, evaluator_model=llm.resolved.model)
    if checkpoint_id:
        bridge.seen_checkpoints.add(checkpoint_id)
    bridge.active_question_ids = {
        question.id for question in _coerce_questions(state.get("sub_questions"))
    }
    prior_events = await _all_owned_run_events(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
    )
    resume_attempt = 1 + sum(
        1 for event in prior_events if event.type == "run_started"
    )
    for event in prior_events:
        if event.type != "subquestion_progressed":
            continue
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        question_id = payload.get("sub_question_id")
        attempt = payload.get("attempt")
        if (
            isinstance(question_id, str)
            and isinstance(attempt, int)
            and not isinstance(attempt, bool)
        ):
            bridge.question_attempts[question_id] = max(
                attempt,
                bridge.question_attempts.get(question_id, 0),
            )
    yield await bridge.run_started(
        boundary=f"resume:{checkpoint_id or 'unknown'}:{resume_attempt}",
        resume_checkpoint_id=checkpoint_id,
    )

    trace = create_trace(
        name="deep_research_resume",
        workspace_id=workspace_id,
        guest_id=guest_id,
        run_id=run_id,
    )
    callback_handler = get_langfuse_callback_handler(trace)
    callbacks = [callback_handler] if callback_handler else None
    final_snapshot: Any | None = None
    try:
        async for event in runtime.astream_events(
            None,
            run_id,
            graph_context,
            callbacks=callbacks,
        ):
            if (
                event.get("event") == "on_chain_start"
                and event.get("name") in _NODE_STAGE_MAP
            ):
                await update_workflow_stage(
                    run_id,
                    stage=_NODE_STAGE_MAP[event["name"]][0],
                )
            for payload in await bridge.handle(event, runtime):
                yield payload

        final_snapshot = await runtime.aget_state(run_id)
        writer.update_state(_snapshot_values(final_snapshot))
        checkpoint_event = await bridge.checkpoint_saved(
            final_snapshot,
            boundary="resume-graph-end",
        )
        if checkpoint_event:
            yield checkpoint_event
        if tuple(getattr(final_snapshot, "next", ()) or ()):
            await _record_execution_stop(
                run_id=run_id,
                workspace_id=workspace_id,
                guest_id=guest_id,
                status=WorkflowRunStatus.interrupted,
                code="execution_interrupted",
            )
            yield await _append_terminal_run_event(
                bridge,
                status="interrupted",
                result=None,
                snapshot=final_snapshot,
                reason_code="execution_interrupted",
                reason="Research paused at a durable checkpoint.",
            )
            return

        final_state = _snapshot_values(final_snapshot)
        await _verify_graph_terminal_persisted(
            run_id=run_id,
            workspace_id=workspace_id,
            guest_id=guest_id,
            state=final_state,
        )
        report = _publishable_report(final_state)
        if report is not None:
            result = _report_to_result(report, run_id)
            yield await _append_terminal_run_event(
                bridge,
                status="completed",
                result=result,
                snapshot=final_snapshot,
                reason_code=None,
                reason=None,
            )
        else:
            result = _incomplete_result(final_state, run_id)
            yield await _append_terminal_run_event(
                bridge,
                status="incomplete",
                result=result,
                snapshot=final_snapshot,
                reason_code=_safe_public_code(final_state.get("workflow_error_code")),
                reason=result.terminal_reason,
            )
    except asyncio.CancelledError:
        try:
            final_snapshot = await runtime.aget_state(run_id)
        except Exception:
            final_snapshot = None
        try:
            committed = await asyncio.shield(
                _committed_terminal_result_or_none(
                    run_id=run_id,
                    workspace_id=workspace_id,
                    guest_id=guest_id,
                )
            )
        except Exception as replay_exc:
            logger.error(
                "dr_resume_terminal_replay_failed",
                error_type=type(replay_exc).__name__,
            )
            committed = None
        try:
            if committed is not None:
                await asyncio.shield(
                    _append_terminal_run_event(
                        bridge,
                        status=committed.status,
                        result=committed,
                        snapshot=final_snapshot,
                        reason_code=None,
                        reason=committed.terminal_reason,
                    )
                )
            else:
                await asyncio.shield(
                    _record_execution_stop(
                        run_id=run_id,
                        workspace_id=workspace_id,
                        guest_id=guest_id,
                        status=WorkflowRunStatus.interrupted,
                        code="execution_interrupted",
                    )
                )
                await asyncio.shield(
                    _append_terminal_run_event(
                        bridge,
                        status="interrupted",
                        result=None,
                        snapshot=final_snapshot,
                        reason_code="execution_interrupted",
                        reason=(
                            "The client disconnected; the latest checkpoint was retained."
                        ),
                    )
                )
        except Exception as persist_exc:
            logger.error(
                "dr_resume_interrupt_persistence_failed",
                error_type=type(persist_exc).__name__,
            )
        raise
    except Exception as exc:
        logger.error("dr_resume_failed", error_type=type(exc).__name__)
        try:
            final_snapshot = await runtime.aget_state(run_id)
        except Exception:
            final_snapshot = None
        try:
            committed = await _committed_terminal_result_or_none(
                run_id=run_id,
                workspace_id=workspace_id,
                guest_id=guest_id,
            )
        except Exception as replay_exc:
            logger.error(
                "dr_resume_terminal_replay_failed",
                error_type=type(replay_exc).__name__,
            )
            committed = None
        if committed is not None:
            yield await _append_terminal_run_event(
                bridge,
                status=committed.status,
                result=committed,
                snapshot=final_snapshot,
                reason_code=None,
                reason=committed.terminal_reason,
            )
            return
        await _record_execution_stop(
            run_id=run_id,
            workspace_id=workspace_id,
            guest_id=guest_id,
            status=WorkflowRunStatus.failed,
            code="resume_failed",
        )
        yield await _append_terminal_run_event(
            bridge,
            status="failed",
            result=None,
            snapshot=final_snapshot,
            reason_code="resume_failed",
            reason="Deep Research could not be resumed from the durable checkpoint.",
        )
    finally:
        await release_lock()


@router.post("/runs/{run_id}/resume/stream")
async def resume_deep_research_stream(
    run_id: str,
    body: DeepResearchResumeRequest,
    guest_id: str = Depends(require_guest_id),
):
    run = await _require_owned_deep_research_run(
        run_id=run_id,
        workspace_id=body.workspace_id,
        guest_id=guest_id,
    )
    status = WorkflowRunStatus(_status_value(run.status))

    if status in _TERMINAL_WORKFLOW_STATUSES:
        result = await _persisted_terminal_result(run, guest_id=guest_id)
        return StreamingResponse(
            _terminal_v1_event_stream(run=run, guest_id=guest_id, result=result),
            media_type="text/event-stream",
        )

    if status not in _RESUMABLE_WORKFLOW_STATUSES:
        raise _conflict(
            "run_not_resumable",
            "This workflow status cannot be resumed.",
        )

    try:
        runtime = get_deep_research_runtime()
        lock_context = deep_research_run_lock(run_id)
        await lock_context.__aenter__()
    except (DeepResearchRuntimeError, DeepResearchRunAlreadyActive, DeepResearchRunLockUnavailable) as exc:
        raise _runtime_conflict(exc) from exc

    lock_released = False

    async def release_lock() -> None:
        nonlocal lock_released
        if lock_released:
            return
        lock_released = True
        await lock_context.__aexit__(None, None, None)

    checkpoint_validated = False
    try:
        locked_run = await _require_owned_deep_research_run(
            run_id=run_id,
            workspace_id=body.workspace_id,
            guest_id=guest_id,
        )
        locked_status = WorkflowRunStatus(_status_value(locked_run.status))
        if locked_status in _TERMINAL_WORKFLOW_STATUSES:
            terminal_result = await _persisted_terminal_result(
                locked_run,
                guest_id=guest_id,
            )
            await release_lock()
            return StreamingResponse(
                _terminal_v1_event_stream(
                    run=locked_run,
                    guest_id=guest_id,
                    result=terminal_result,
                ),
                media_type="text/event-stream",
            )
        if locked_status not in _RESUMABLE_WORKFLOW_STATUSES:
            raise _conflict(
                "run_not_resumable",
                "This workflow status cannot be resumed.",
            )
        snapshot = await runtime.aget_state(run_id)
        next_nodes = tuple(getattr(snapshot, "next", ()) or ())
        if not next_nodes:
            raise _conflict(
                "checkpoint_not_restorable",
                "The checkpoint has no remaining executable nodes.",
            )
        checkpoint_validated = True
        llm = await _resolve_llm(guest_id)
        graph_context = _build_graph_context(
            run_id=run_id,
            workspace_id=body.workspace_id,
            guest_id=guest_id,
            llm=llm,
            artifact_recorder=PostgresArtifactRecorder(),
            artifact_persistence_required=True,
        )
        active_run = await mark_workflow_run_running(
            run_id=run_id,
            workspace_id=body.workspace_id,
            guest_id=guest_id,
        )
        if active_run is None or active_run.status != WorkflowRunStatus.running:
            raise RuntimeError("The owned workflow run could not be marked running.")
    except (DeepResearchCheckpointNotFound, DeepResearchGraphVersionMismatch) as exc:
        await release_lock()
        raise _runtime_conflict(exc) from exc
    except HTTPException:
        await release_lock()
        raise
    except asyncio.CancelledError:
        try:
            await asyncio.shield(
                _record_execution_stop(
                    run_id=run_id,
                    workspace_id=body.workspace_id,
                    guest_id=guest_id,
                    status=WorkflowRunStatus.interrupted,
                    code="execution_interrupted",
                )
            )
        finally:
            await asyncio.shield(release_lock())
        raise
    except Exception as exc:
        if checkpoint_validated:
            try:
                await _record_execution_stop(
                    run_id=run_id,
                    workspace_id=body.workspace_id,
                    guest_id=guest_id,
                    status=WorkflowRunStatus.failed,
                    code="resume_preflight_failed",
                )
            except Exception as persist_exc:
                logger.error(
                    "dr_resume_preflight_persistence_failed",
                    error_type=type(persist_exc).__name__,
                )
        await release_lock()
        if isinstance(exc, DeepResearchRuntimeError):
            raise _runtime_conflict(exc) from exc
        raise HTTPException(
            status_code=503,
            detail={
                "code": "resume_preflight_failed",
                "message": "Deep Research resume could not be initialized.",
            },
        ) from exc

    return StreamingResponse(
        _resume_v1_event_stream(
            run_id=run_id,
            workspace_id=body.workspace_id,
            guest_id=guest_id,
            runtime=runtime,
            graph_context=graph_context,
            llm=llm,
            initial_snapshot=snapshot,
            release_lock=release_lock,
        ),
        media_type="text/event-stream",
        background=BackgroundTask(release_lock),
    )
