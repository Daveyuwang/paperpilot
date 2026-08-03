"""Append-only, ownership-scoped Deep Research event persistence.

The event stream is the durable replay contract for Research Console.  It is
separate from LangGraph checkpoints: checkpoints recover execution, while
these ordered envelopes explain durable workflow progress to users.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Annotated, Any, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deep_research.artifacts import validate_artifact_payload
from app.models.orm import (
    DeepResearchRunEvent,
    WorkflowRun,
    WorkflowRunType,
    Workspace,
)


DEEP_RESEARCH_EVENT_SCHEMA_VERSION = "deep-research-event.v1"
DEEP_RESEARCH_EVENT_TYPES = frozenset(
    {
        "run_started",
        "phase_started",
        "phase_completed",
        "subquestion_upserted",
        "subquestion_progressed",
        "evaluation_started",
        "evaluation_completed",
        "route_selected",
        "artifact_version_created",
        "checkpoint_saved",
        "budget_updated",
        "synthesis_section_updated",
        "run_finished",
        "protocol_error",
    }
)
DEFAULT_EVENT_PAGE_SIZE = 200
MAX_EVENT_PAGE_SIZE = 1000
_MAX_ALLOCATION_ATTEMPTS = 5


class DeepResearchEventStoreError(RuntimeError):
    """Base error for fail-closed run-event persistence."""


class DeepResearchEventOwnershipError(DeepResearchEventStoreError):
    """The requested Deep Research run is absent from the ownership scope."""


class DeepResearchEventConflictError(DeepResearchEventStoreError):
    """An event id was reused for different data or sequence allocation failed."""


class DeepResearchEventIntegrityError(DeepResearchEventStoreError):
    """A stored event violates the immutable event contract."""


class DeepResearchEventPayloadError(DeepResearchEventStoreError):
    """An event payload does not match its v1 discriminated schema."""


NonEmptyStr = Annotated[str, Field(min_length=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]
NonNegativeNumber = Annotated[int | float, Field(ge=0, allow_inf_nan=False)]
ScoreNumber = Annotated[
    int | float,
    Field(ge=0, le=100, allow_inf_nan=False),
]


class _EventPayloadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _BudgetPayload(_EventPayloadModel):
    pre_evaluations_used: NonNegativeInt
    targeted_repairs_used: NonNegativeInt
    partial_replans_used: NonNegativeInt
    full_replans_used: NonNegativeInt
    total_recoveries_used: NonNegativeInt
    post_evaluations_used: NonNegativeInt
    synthesis_repairs_used: NonNegativeInt
    pre_evaluation_limit: NonNegativeInt
    targeted_repair_limit: NonNegativeInt
    partial_replan_limit: NonNegativeInt
    full_replan_limit: NonNegativeInt
    total_recovery_limit: NonNegativeInt
    post_evaluation_limit: NonNegativeInt
    synthesis_repair_limit: NonNegativeInt


class _ResumePayload(_EventPayloadModel):
    allowed: bool
    checkpoint_id: NonEmptyStr | None
    reason_code: NonEmptyStr
    reason: NonEmptyStr


class _RunStartedPayload(_EventPayloadModel):
    workspace_id: NonEmptyStr
    topic: NonEmptyStr
    graph_version: NonEmptyStr
    status: Literal["running"]
    budget: _BudgetPayload
    resume: _ResumePayload


ResearchPhase = Literal[
    "validating",
    "planning",
    "executing",
    "pre_synthesis_evaluation",
    "routing",
    "targeted_repair",
    "partial_replan",
    "full_replan",
    "synthesizing",
    "post_synthesis_evaluation",
    "report_revision",
    "finalizing",
]
EvaluationPhase = Literal["pre_synthesis", "post_synthesis"]


class _PhasePayload(_EventPayloadModel):
    phase: ResearchPhase
    node: NonEmptyStr
    label: NonEmptyStr
    round_id: NonEmptyStr | None
    evaluation_phase: EvaluationPhase | None
    target_sub_question_ids: list[str]
    target_report_segment_ids: list[str]
    output_artifact_version_ids: list[str]
    duration_ms: NonNegativeNumber | None
    status: Literal["completed", "failed"] = Field(default=None)  # type: ignore[assignment]


SubQuestionStatus = Literal[
    "pending",
    "in_progress",
    "completed",
    "failed",
    "superseded",
]
SubQuestionOrigin = Literal[
    "initial",
    "targeted_repair",
    "partial_replan",
    "full_replan",
]


class _SubQuestionPayload(_EventPayloadModel):
    id: NonEmptyStr
    question: NonEmptyStr
    priority: NonNegativeInt
    order: NonNegativeInt
    plan_version: NonNegativeInt
    origin: SubQuestionOrigin
    status: SubQuestionStatus
    attempt: NonNegativeInt
    confidence: NonNegativeNumber | None
    duration_ms: NonNegativeNumber | None
    error_code: str | None
    error_message: str | None
    sub_report_artifact_version_id: NonEmptyStr | None


class _SubQuestionUpsertedPayload(_EventPayloadModel):
    sub_question: _SubQuestionPayload


class _SubQuestionProgressedPayload(_EventPayloadModel):
    sub_question_id: NonEmptyStr
    status: SubQuestionStatus
    attempt: NonNegativeInt
    confidence: NonNegativeNumber | None
    duration_ms: NonNegativeNumber | None
    error_code: str | None
    error_message: str | None
    sub_report_artifact_version_id: NonEmptyStr | None


class _EvaluationSubjectPayload(_EventPayloadModel):
    kind: Literal["corpus", "report"]
    digest: NonEmptyStr
    version: NonNegativeInt


class _EvaluationStartedPayload(_EventPayloadModel):
    evaluation_id: NonEmptyStr
    round_id: NonEmptyStr
    phase: EvaluationPhase
    subject: _EvaluationSubjectPayload
    evaluator_model: NonEmptyStr


class _EvaluationIssuePayload(_EventPayloadModel):
    id: NonEmptyStr
    category: NonEmptyStr
    severity: Literal["minor", "major", "blocker"]
    suggested_repair_stage: str | None
    affected_sub_question_ids: list[str]
    claim_ids: list[str]
    segment_ids: list[str]


class _EvaluationCompletedPayload(_EventPayloadModel):
    evaluation_id: NonEmptyStr
    round_id: NonEmptyStr
    phase: EvaluationPhase
    status: Literal["completed", "failed"]
    subject: _EvaluationSubjectPayload
    evaluator_model: NonEmptyStr
    attempts: NonNegativeInt
    duration_ms: NonNegativeNumber
    scores: dict[str, ScoreNumber]
    issues: list[_EvaluationIssuePayload]
    summary: str | None
    error_code: str | None
    artifact_version_id: NonEmptyStr | None


ResearchRoute = Literal[
    "accept",
    "targeted_repair",
    "partial_replan",
    "full_replan",
    "stop_incomplete",
]


class _RouteSelectedPayload(_EventPayloadModel):
    decision_id: NonEmptyStr
    round_id: NonEmptyStr
    evaluation_id: NonEmptyStr
    phase: EvaluationPhase
    route: ResearchRoute
    repair_stage: str | None
    weighted_overall_score: NonNegativeNumber | None
    reason_code: NonEmptyStr
    reason: NonEmptyStr
    target_sub_question_ids: list[str]
    target_report_segment_ids: list[str]
    budget: _BudgetPayload
    artifact_version_id: NonEmptyStr | None


ArtifactKind = Literal[
    "plan",
    "sub_report",
    "pre_synthesis_evaluation",
    "controller_transition",
    "report_candidate",
    "post_synthesis_evaluation",
    "terminal_decision",
]


class _ArtifactPayload(_EventPayloadModel):
    id: NonEmptyStr
    run_id: NonEmptyStr
    artifact_kind: ArtifactKind
    logical_artifact_id: NonEmptyStr
    version_number: PositiveInt
    plan_version: NonNegativeInt
    controller_cycle: NonNegativeInt
    schema_version: PositiveInt
    parent_version_id: NonEmptyStr | None
    source_checkpoint_id: NonEmptyStr | None
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    created_at: NonEmptyStr
    payload: dict[str, Any] = Field(default_factory=dict)


class _ArtifactVersionCreatedPayload(_EventPayloadModel):
    artifact: _ArtifactPayload


class _CheckpointPayload(_EventPayloadModel):
    checkpoint_id: NonEmptyStr
    graph_version: NonEmptyStr
    restorable: bool
    saved_at: NonEmptyStr
    next_nodes: list[str]


class _CheckpointSavedPayload(_EventPayloadModel):
    checkpoint: _CheckpointPayload
    resume: _ResumePayload


class _BudgetUpdatedPayload(_EventPayloadModel):
    budget: _BudgetPayload
    cause: NonEmptyStr


class _SegmentPayload(_EventPayloadModel):
    segment_id: NonEmptyStr
    title: NonEmptyStr
    status: Literal["pending", "writing", "completed", "failed"]
    report_version: PositiveInt
    duration_ms: NonNegativeNumber | None
    artifact_version_id: NonEmptyStr | None


class _SynthesisSectionUpdatedPayload(_EventPayloadModel):
    segment: _SegmentPayload


class _RunFinishedPayload(_EventPayloadModel):
    status: Literal["interrupted", "completed", "incomplete", "failed"]
    report_accepted: bool
    publishable: bool
    terminal_reason_code: str | None
    terminal_reason: str | None
    candidate_artifact_version_id: NonEmptyStr | None
    final_artifact_version_id: NonEmptyStr | None
    deliverable_id: NonEmptyStr | None
    result: dict[str, Any] | None
    resume: _ResumePayload


class _ProtocolErrorPayload(_EventPayloadModel):
    code: NonEmptyStr
    message: NonEmptyStr
    recoverable: bool
    last_good_seq: NonNegativeInt


_EVENT_PAYLOAD_MODELS: dict[str, type[_EventPayloadModel]] = {
    "run_started": _RunStartedPayload,
    "phase_started": _PhasePayload,
    "phase_completed": _PhasePayload,
    "subquestion_upserted": _SubQuestionUpsertedPayload,
    "subquestion_progressed": _SubQuestionProgressedPayload,
    "evaluation_started": _EvaluationStartedPayload,
    "evaluation_completed": _EvaluationCompletedPayload,
    "route_selected": _RouteSelectedPayload,
    "artifact_version_created": _ArtifactVersionCreatedPayload,
    "checkpoint_saved": _CheckpointSavedPayload,
    "budget_updated": _BudgetUpdatedPayload,
    "synthesis_section_updated": _SynthesisSectionUpdatedPayload,
    "run_finished": _RunFinishedPayload,
    "protocol_error": _ProtocolErrorPayload,
}


def _require_string(name: str, value: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty, trimmed string")
    if len(value) > max_length:
        raise ValueError(f"{name} must contain at most {max_length} characters")
    return value


def _require_optional_integer(
    name: str,
    value: int | None,
    *,
    minimum: int,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be null or an integer >= {minimum}")
    return value


def _require_integer(name: str, value: int, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _require_event_id(value: str | None) -> str:
    if value is None:
        return str(uuid.uuid4())
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("event_id must be a non-empty, trimmed UUID")
    try:
        return str(uuid.UUID(value))
    except (AttributeError, ValueError) as exc:
        raise ValueError("event_id must be a valid UUID") from exc


def _require_event_type(value: str) -> str:
    event_type = _require_string("type", value, max_length=128)
    if event_type not in DEEP_RESEARCH_EVENT_TYPES:
        raise ValueError(f"unsupported Deep Research event type: {event_type!r}")
    return event_type


def _normalize_emitted_at(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise ValueError("emitted_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("emitted_at must include a timezone")
    return value.astimezone(timezone.utc)


def _normalize_stored_emitted_at(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise DeepResearchEventIntegrityError(
            "stored event has an invalid emitted_at timestamp"
        )
    # SQLite drops timezone information from DateTime values. Its migration
    # compatibility path is interpreted as UTC; PostgreSQL preserves +00:00.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_payload_json(payload: Mapping[str, Any]) -> str:
    # Reuse the artifact store's conservative credential/private-runtime gate
    # so events and artifact snapshots have exactly the same safety boundary.
    validate_artifact_payload(payload)
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("event payload must be canonical JSON") from exc


def _canonical_payload_copy(payload: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(_canonical_payload_json(payload))


def validate_run_event_payload(
    type: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and canonicalize one payload against its v1 event schema."""

    event_type = _require_event_type(type)
    canonical_payload = _canonical_payload_copy(payload)
    model_type = _EVENT_PAYLOAD_MODELS[event_type]
    try:
        validated = model_type.model_validate(canonical_payload)
    except ValidationError as exc:
        raise DeepResearchEventPayloadError(
            f"payload does not match the {event_type} v1 schema"
        ) from exc
    normalized = validated.model_dump(mode="json", exclude_unset=True)
    validate_artifact_payload(normalized)
    return normalized


async def _require_owned_run(
    db: AsyncSession,
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    lock_for_append: bool,
) -> WorkflowRun:
    statement = (
        select(WorkflowRun)
        .join(Workspace, WorkflowRun.workspace_id == Workspace.id)
        .where(
            WorkflowRun.id == run_id,
            WorkflowRun.workspace_id == workspace_id,
            WorkflowRun.guest_id == guest_id,
            WorkflowRun.run_type == WorkflowRunType.deep_research,
            Workspace.id == workspace_id,
            Workspace.guest_id == guest_id,
        )
    )
    if lock_for_append:
        # Serializing on the parent row makes MAX(seq) + 1 safe on PostgreSQL.
        # SQLite ignores FOR UPDATE but still remains compatible; the unique
        # constraint plus bounded retry handles an allocation race there.
        statement = statement.with_for_update(of=WorkflowRun)
    run = await db.scalar(statement)
    if run is None:
        raise DeepResearchEventOwnershipError(
            "Deep Research run not found in ownership scope"
        )
    return run


async def _find_event(
    db: AsyncSession,
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    event_id: str,
) -> DeepResearchRunEvent | None:
    return await db.scalar(
        select(DeepResearchRunEvent).where(
            DeepResearchRunEvent.run_id == run_id,
            DeepResearchRunEvent.workspace_id == workspace_id,
            DeepResearchRunEvent.guest_id == guest_id,
            DeepResearchRunEvent.event_id == event_id,
        )
    )


def _verify_stored_event(event: DeepResearchRunEvent) -> None:
    try:
        normalized_event_id = str(uuid.UUID(event.event_id))
    except (AttributeError, ValueError) as exc:
        raise DeepResearchEventIntegrityError(
            "stored event has an invalid event id"
        ) from exc
    if normalized_event_id != event.event_id:
        raise DeepResearchEventIntegrityError(
            "stored event id is not in canonical UUID form"
        )
    if (
        isinstance(event.seq, bool)
        or not isinstance(event.seq, int)
        or event.seq < 1
    ):
        raise DeepResearchEventIntegrityError(
            "stored event has an invalid sequence number"
        )
    if event.schema_version != DEEP_RESEARCH_EVENT_SCHEMA_VERSION:
        raise DeepResearchEventIntegrityError(
            "stored event has an unsupported schema version"
        )
    if event.type not in DEEP_RESEARCH_EVENT_TYPES:
        raise DeepResearchEventIntegrityError(
            "stored event has an unsupported event type"
        )
    _normalize_stored_emitted_at(event.emitted_at)
    try:
        _require_integer("cycle", event.cycle, minimum=0)
        _require_integer("plan_version", event.plan_version, minimum=0)
        _require_integer("corpus_version", event.corpus_version, minimum=0)
        _require_optional_integer("report_version", event.report_version, minimum=1)
        if event.checkpoint_id is not None:
            _require_string(
                "checkpoint_id", event.checkpoint_id, max_length=255
            )
            validate_artifact_payload({"checkpoint_id": event.checkpoint_id})
    except ValueError as exc:
        raise DeepResearchEventIntegrityError(
            "stored event has invalid workflow version metadata"
        ) from exc
    try:
        validate_run_event_payload(event.type, event.payload)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise DeepResearchEventIntegrityError(
            "stored event violates the payload safety contract"
        ) from exc


@dataclass(frozen=True, slots=True)
class _EventIdentity:
    type: str
    cycle: int
    plan_version: int
    corpus_version: int
    report_version: int | None
    checkpoint_id: str | None
    payload_json: str


def _resolve_idempotent_event(
    existing: DeepResearchRunEvent,
    *,
    expected: _EventIdentity,
    expected_emitted_at: datetime | None,
) -> DeepResearchRunEvent:
    _verify_stored_event(existing)
    actual = _EventIdentity(
        type=existing.type,
        cycle=existing.cycle,
        plan_version=existing.plan_version,
        corpus_version=existing.corpus_version,
        report_version=existing.report_version,
        checkpoint_id=existing.checkpoint_id,
        payload_json=_canonical_payload_json(existing.payload),
    )
    if actual != expected:
        raise DeepResearchEventConflictError(
            "event id was already used for different content or metadata"
        )
    if (
        expected_emitted_at is not None
        and _normalize_stored_emitted_at(existing.emitted_at)
        != expected_emitted_at
    ):
        raise DeepResearchEventConflictError(
            "event id was already used with a different emitted_at timestamp"
        )
    return existing


async def _allocate_next_seq(
    db: AsyncSession,
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
) -> int:
    value = await db.scalar(
        select(func.coalesce(func.max(DeepResearchRunEvent.seq), 0) + 1).where(
            DeepResearchRunEvent.run_id == run_id,
            DeepResearchRunEvent.workspace_id == workspace_id,
            DeepResearchRunEvent.guest_id == guest_id,
        )
    )
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DeepResearchEventIntegrityError(
            "could not allocate a valid run event sequence"
        )
    return value


async def append_run_event(
    db: AsyncSession,
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    type: str,
    payload: Mapping[str, Any],
    cycle: int,
    plan_version: int,
    corpus_version: int,
    event_id: str | None = None,
    emitted_at: datetime | None = None,
    report_version: int | None = None,
    checkpoint_id: str | None = None,
) -> DeepResearchRunEvent:
    """Append one event without committing the caller's transaction.

    A caller-supplied ``event_id`` is an idempotency identity. Replaying it
    with the same envelope returns the existing row; any conflicting reuse
    fails closed. Sequence allocation occurs while the owned WorkflowRun row
    is locked, and the unique constraint plus bounded retry protects database
    backends that cannot honor row-level ``FOR UPDATE``.
    """

    run_id = _require_string("run_id", run_id, max_length=36)
    workspace_id = _require_string("workspace_id", workspace_id, max_length=36)
    guest_id = _require_string("guest_id", guest_id, max_length=64)
    event_type = _require_event_type(type)
    normalized_event_id = _require_event_id(event_id)
    supplied_emitted_at = (
        _normalize_emitted_at(emitted_at) if emitted_at is not None else None
    )
    event_emitted_at = supplied_emitted_at or _normalize_emitted_at(None)
    cycle = _require_integer("cycle", cycle, minimum=0)
    plan_version = _require_integer("plan_version", plan_version, minimum=0)
    corpus_version = _require_integer(
        "corpus_version", corpus_version, minimum=0
    )
    report_version = _require_optional_integer(
        "report_version", report_version, minimum=1
    )
    if checkpoint_id is not None:
        checkpoint_id = _require_string(
            "checkpoint_id", checkpoint_id, max_length=255
        )
        validate_artifact_payload({"checkpoint_id": checkpoint_id})
    canonical_payload = validate_run_event_payload(event_type, payload)
    identity = _EventIdentity(
        type=event_type,
        cycle=cycle,
        plan_version=plan_version,
        corpus_version=corpus_version,
        report_version=report_version,
        checkpoint_id=checkpoint_id,
        payload_json=_canonical_payload_json(canonical_payload),
    )

    await _require_owned_run(
        db,
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        lock_for_append=True,
    )
    existing = await _find_event(
        db,
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        event_id=normalized_event_id,
    )
    if existing is not None:
        return _resolve_idempotent_event(
            existing,
            expected=identity,
            expected_emitted_at=supplied_emitted_at,
        )

    for attempt in range(_MAX_ALLOCATION_ATTEMPTS):
        seq = await _allocate_next_seq(
            db,
            run_id=run_id,
            workspace_id=workspace_id,
            guest_id=guest_id,
        )
        event = DeepResearchRunEvent(
            run_id=run_id,
            workspace_id=workspace_id,
            guest_id=guest_id,
            seq=seq,
            event_id=normalized_event_id,
            schema_version=DEEP_RESEARCH_EVENT_SCHEMA_VERSION,
            type=event_type,
            emitted_at=event_emitted_at,
            cycle=cycle,
            plan_version=plan_version,
            corpus_version=corpus_version,
            report_version=report_version,
            checkpoint_id=checkpoint_id,
            payload=canonical_payload,
        )
        try:
            async with db.begin_nested():
                db.add(event)
                await db.flush()
        except IntegrityError as exc:
            concurrent = await _find_event(
                db,
                run_id=run_id,
                workspace_id=workspace_id,
                guest_id=guest_id,
                event_id=normalized_event_id,
            )
            if concurrent is not None:
                return _resolve_idempotent_event(
                    concurrent,
                    expected=identity,
                    expected_emitted_at=supplied_emitted_at,
                )
            if attempt + 1 == _MAX_ALLOCATION_ATTEMPTS:
                raise DeepResearchEventConflictError(
                    "could not allocate a unique run event sequence"
                ) from exc
            continue
        return event

    raise DeepResearchEventConflictError(
        "could not allocate a unique run event sequence"
    )


async def list_run_events(
    db: AsyncSession,
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    after_seq: int = 0,
    limit: int = DEFAULT_EVENT_PAGE_SIZE,
) -> list[DeepResearchRunEvent]:
    """Return one bounded, ordered replay page in an exact ownership scope."""

    run_id = _require_string("run_id", run_id, max_length=36)
    workspace_id = _require_string("workspace_id", workspace_id, max_length=36)
    guest_id = _require_string("guest_id", guest_id, max_length=64)
    if (
        isinstance(after_seq, bool)
        or not isinstance(after_seq, int)
        or after_seq < 0
    ):
        raise ValueError("after_seq must be an integer >= 0")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
        or limit > MAX_EVENT_PAGE_SIZE
    ):
        raise ValueError(
            f"limit must be an integer between 1 and {MAX_EVENT_PAGE_SIZE}"
        )
    await _require_owned_run(
        db,
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        lock_for_append=False,
    )
    result = await db.scalars(
        select(DeepResearchRunEvent)
        .where(
            DeepResearchRunEvent.run_id == run_id,
            DeepResearchRunEvent.workspace_id == workspace_id,
            DeepResearchRunEvent.guest_id == guest_id,
            DeepResearchRunEvent.seq > after_seq,
        )
        .order_by(DeepResearchRunEvent.seq)
        .limit(limit)
    )
    events = list(result.all())
    for event in events:
        _verify_stored_event(event)
    return events


__all__ = [
    "DEEP_RESEARCH_EVENT_SCHEMA_VERSION",
    "DEEP_RESEARCH_EVENT_TYPES",
    "DEFAULT_EVENT_PAGE_SIZE",
    "MAX_EVENT_PAGE_SIZE",
    "DeepResearchEventConflictError",
    "DeepResearchEventIntegrityError",
    "DeepResearchEventOwnershipError",
    "DeepResearchEventPayloadError",
    "DeepResearchEventStoreError",
    "append_run_event",
    "list_run_events",
    "validate_run_event_payload",
]
