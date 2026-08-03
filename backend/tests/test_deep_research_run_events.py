from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.deep_research import events as event_store
from app.deep_research.artifacts import UnsafeArtifactPayloadError
from app.deep_research.events import (
    DEEP_RESEARCH_EVENT_SCHEMA_VERSION,
    DEEP_RESEARCH_EVENT_TYPES,
    DeepResearchEventConflictError,
    DeepResearchEventIntegrityError,
    DeepResearchEventOwnershipError,
    DeepResearchEventPayloadError,
    append_run_event,
    list_run_events,
    validate_run_event_payload,
)
from app.models.orm import (
    DeepResearchRunEvent,
    ImmutableDeepResearchRunEventError,
    WorkflowRunType,
)


RUN_ID = "96eff9be-5338-44dd-82cc-b22d61046619"
WORKSPACE_ID = "16c7fc3c-6fe2-40fe-9b84-98b980479c42"
GUEST_ID = "guest-event-tests"
EVENT_ID = "b0b27ec4-e0ab-457b-87a8-8cdef5d0d3f9"
EMITTED_AT = datetime(2026, 7, 31, 8, 30, 0, tzinfo=timezone.utc)

BUDGET = {
    "pre_evaluations_used": 1,
    "targeted_repairs_used": 0,
    "partial_replans_used": 0,
    "full_replans_used": 0,
    "total_recoveries_used": 0,
    "post_evaluations_used": 0,
    "synthesis_repairs_used": 0,
    "pre_evaluation_limit": 5,
    "targeted_repair_limit": 2,
    "partial_replan_limit": 1,
    "full_replan_limit": 1,
    "total_recovery_limit": 4,
    "post_evaluation_limit": 4,
    "synthesis_repair_limit": 2,
}
RESUME = {
    "allowed": True,
    "checkpoint_id": "checkpoint-7",
    "reason_code": "checkpoint_available",
    "reason": "A durable checkpoint is available.",
}
SUB_QUESTION = {
    "id": "sq-1",
    "question": "What changed?",
    "priority": 1,
    "order": 0,
    "plan_version": 3,
    "origin": "initial",
    "status": "completed",
    "attempt": 1,
    "confidence": 0.91,
    "duration_ms": 1200,
    "error_code": None,
    "error_message": None,
    "sub_report_artifact_version_id": "artifact-sub-1",
}
SUBJECT = {"kind": "corpus", "digest": "digest-1", "version": 4}
ISSUE = {
    "id": "issue-1",
    "category": "coverage",
    "severity": "major",
    "suggested_repair_stage": "targeted_repair",
    "affected_sub_question_ids": ["sq-1"],
    "claim_ids": [],
    "segment_ids": [],
}
ARTIFACT = {
    "id": "artifact-1",
    "run_id": RUN_ID,
    "artifact_kind": "plan",
    "logical_artifact_id": "active-plan",
    "version_number": 1,
    "plan_version": 3,
    "controller_cycle": 2,
    "schema_version": 1,
    "parent_version_id": None,
    "source_checkpoint_id": "checkpoint-7",
    "content_hash": "a" * 64,
    "created_at": "2026-07-31T08:30:00Z",
}
CHECKPOINT = {
    "checkpoint_id": "checkpoint-7",
    "graph_version": "deep-research.v1",
    "restorable": True,
    "saved_at": "2026-07-31T08:30:00Z",
    "next_nodes": ["execute"],
}
SEGMENT = {
    "segment_id": "executive-summary",
    "title": "Executive summary",
    "status": "completed",
    "report_version": 1,
    "duration_ms": 900,
    "artifact_version_id": "artifact-report-1",
}
VALID_PAYLOADS = {
    "run_started": {
        "workspace_id": WORKSPACE_ID,
        "topic": "Deep Research agents",
        "graph_version": "deep-research.v1",
        "status": "running",
        "budget": BUDGET,
        "resume": RESUME,
    },
    "phase_started": {
        "phase": "executing",
        "node": "execute",
        "label": "Execute subquestions",
        "round_id": None,
        "evaluation_phase": None,
        "target_sub_question_ids": ["sq-1"],
        "target_report_segment_ids": [],
        "output_artifact_version_ids": [],
        "duration_ms": None,
    },
    "phase_completed": {
        "phase": "executing",
        "node": "execute",
        "label": "Execute subquestions",
        "round_id": None,
        "evaluation_phase": None,
        "target_sub_question_ids": ["sq-1"],
        "target_report_segment_ids": [],
        "output_artifact_version_ids": ["artifact-sub-1"],
        "duration_ms": 1200,
        "status": "completed",
    },
    "subquestion_upserted": {"sub_question": SUB_QUESTION},
    "subquestion_progressed": {
        "sub_question_id": "sq-1",
        "status": "completed",
        "attempt": 1,
        "confidence": 0.91,
        "duration_ms": 1200,
        "error_code": None,
        "error_message": None,
        "sub_report_artifact_version_id": "artifact-sub-1",
    },
    "evaluation_started": {
        "evaluation_id": "evaluation-1",
        "round_id": "round-1",
        "phase": "pre_synthesis",
        "subject": SUBJECT,
        "evaluator_model": "gpt-5",
    },
    "evaluation_completed": {
        "evaluation_id": "evaluation-1",
        "round_id": "round-1",
        "phase": "pre_synthesis",
        "status": "completed",
        "subject": SUBJECT,
        "evaluator_model": "gpt-5",
        "attempts": 1,
        "duration_ms": 830,
        "scores": {"coverage": 81.5},
        "issues": [ISSUE],
        "summary": "One repair is required.",
        "error_code": None,
        "artifact_version_id": "artifact-eval-1",
    },
    "route_selected": {
        "decision_id": "decision-1",
        "round_id": "round-1",
        "evaluation_id": "evaluation-1",
        "phase": "pre_synthesis",
        "route": "targeted_repair",
        "repair_stage": "targeted_repair",
        "weighted_overall_score": 71.0,
        "reason_code": "coverage_gap",
        "reason": "One subquestion needs more evidence.",
        "target_sub_question_ids": ["sq-1"],
        "target_report_segment_ids": [],
        "budget": BUDGET,
        "artifact_version_id": "artifact-route-1",
    },
    "artifact_version_created": {"artifact": ARTIFACT},
    "checkpoint_saved": {"checkpoint": CHECKPOINT, "resume": RESUME},
    "budget_updated": {"budget": BUDGET, "cause": "targeted_repair"},
    "synthesis_section_updated": {"segment": SEGMENT},
    "run_finished": {
        "status": "completed",
        "report_accepted": True,
        "publishable": True,
        "terminal_reason_code": None,
        "terminal_reason": None,
        "candidate_artifact_version_id": "artifact-report-1",
        "final_artifact_version_id": "artifact-report-1",
        "deliverable_id": "deliverable-1",
        "result": {"title": "Research result"},
        "resume": RESUME,
    },
    "protocol_error": {
        "code": "invalid_event",
        "message": "An invalid event was rejected.",
        "recoverable": True,
        "last_good_seq": 4,
    },
}


class _NestedTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        return False


def _owned_run() -> SimpleNamespace:
    return SimpleNamespace(
        id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
        run_type=WorkflowRunType.deep_research,
    )


def _db_with_scalar_results(*values: object) -> MagicMock:
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=list(values))
    db.scalars = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    db.begin_nested = MagicMock(return_value=_NestedTransaction())
    return db


def _event(
    *,
    seq: int = 1,
    event_id: str = EVENT_ID,
    payload: dict | None = None,
    emitted_at: datetime = EMITTED_AT,
    event_type: str = "protocol_error",
) -> DeepResearchRunEvent:
    return DeepResearchRunEvent(
        id=str(uuid.uuid4()),
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
        seq=seq,
        event_id=event_id,
        schema_version=DEEP_RESEARCH_EVENT_SCHEMA_VERSION,
        type=event_type,
        emitted_at=emitted_at,
        cycle=2,
        plan_version=3,
        corpus_version=4,
        report_version=1,
        checkpoint_id="checkpoint-7",
        payload=payload or VALID_PAYLOADS["protocol_error"],
    )


def _append_kwargs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "workspace_id": WORKSPACE_ID,
        "guest_id": GUEST_ID,
        "event_id": EVENT_ID,
        "type": "protocol_error",
        "emitted_at": EMITTED_AT,
        "cycle": 2,
        "plan_version": 3,
        "corpus_version": 4,
        "report_version": 1,
        "checkpoint_id": "checkpoint-7",
        "payload": VALID_PAYLOADS["protocol_error"],
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_append_allocates_first_sequence_under_exact_owned_run_lock() -> None:
    db = _db_with_scalar_results(_owned_run(), None, 1)

    created = await append_run_event(db, **_append_kwargs())

    assert created.seq == 1
    assert created.event_id == EVENT_ID
    assert created.schema_version == DEEP_RESEARCH_EVENT_SCHEMA_VERSION
    assert created.emitted_at == EMITTED_AT
    assert created.payload == VALID_PAYLOADS["protocol_error"]
    assert db.add.call_args.args[0] is created
    db.flush.assert_awaited_once()

    ownership_statement = db.scalar.await_args_list[0].args[0]
    assert ownership_statement._for_update_arg is not None
    postgres_sql = str(
        ownership_statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FOR UPDATE OF workflow_runs" in postgres_sql
    assert "workflow_runs.run_type" in postgres_sql
    assert "workflow_runs.workspace_id" in postgres_sql
    assert "workflow_runs.guest_id" in postgres_sql
    assert "workspaces.guest_id" in postgres_sql


@pytest.mark.asyncio
async def test_exact_event_replay_is_idempotent_without_reallocation() -> None:
    existing = _event()
    db = _db_with_scalar_results(_owned_run(), existing)

    replay = await append_run_event(db, **_append_kwargs())

    assert replay is existing
    assert db.scalar.await_count == 2
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_server_timestamp_is_stable_when_replay_omits_emitted_at() -> None:
    existing = _event()
    db = _db_with_scalar_results(_owned_run(), existing)
    kwargs = _append_kwargs()
    kwargs.pop("emitted_at")

    replay = await append_run_event(db, **kwargs)

    assert replay is existing


@pytest.mark.asyncio
async def test_conflicting_event_id_reuse_fails_closed() -> None:
    existing = _event()
    db = _db_with_scalar_results(_owned_run(), existing)

    with pytest.raises(DeepResearchEventConflictError):
        await append_run_event(
            db,
            **_append_kwargs(
                payload={
                    **VALID_PAYLOADS["protocol_error"],
                    "message": "A different error occurred.",
                }
            ),
        )

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_exact_replay_distinguishes_json_number_types() -> None:
    integer_payload = {
        **VALID_PAYLOADS["route_selected"],
        "weighted_overall_score": 71,
    }
    float_payload = {
        **VALID_PAYLOADS["route_selected"],
        "weighted_overall_score": 71.0,
    }
    existing = _event(
        payload=integer_payload,
        event_type="route_selected",
    )
    db = _db_with_scalar_results(_owned_run(), existing)

    with pytest.raises(DeepResearchEventConflictError):
        await append_run_event(
            db,
            **_append_kwargs(type="route_selected", payload=float_payload),
        )


@pytest.mark.asyncio
async def test_conflicting_explicit_emitted_at_fails_closed() -> None:
    existing = _event()
    db = _db_with_scalar_results(_owned_run(), existing)

    with pytest.raises(DeepResearchEventConflictError):
        await append_run_event(
            db,
            **_append_kwargs(
                emitted_at=EMITTED_AT + timedelta(seconds=1)
            ),
        )


@pytest.mark.asyncio
async def test_sequence_collision_retries_allocation_inside_transaction() -> None:
    db = _db_with_scalar_results(_owned_run(), None, 1, None, 2)
    db.flush.side_effect = [
        IntegrityError(
            "duplicate sequence",
            params={},
            orig=RuntimeError("unique violation"),
        ),
        None,
    ]

    created = await append_run_event(db, **_append_kwargs())

    assert created.seq == 2
    assert db.add.call_count == 2
    assert db.flush.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_exact_append_resolves_to_existing_event() -> None:
    existing = _event()
    db = _db_with_scalar_results(_owned_run(), None, 1, existing)
    db.flush.side_effect = IntegrityError(
        "duplicate event id",
        params={},
        orig=RuntimeError("unique violation"),
    )

    actual = await append_run_event(db, **_append_kwargs())

    assert actual is existing


@pytest.mark.asyncio
async def test_append_requires_run_workspace_guest_and_deep_research_scope() -> None:
    db = _db_with_scalar_results(None)

    with pytest.raises(DeepResearchEventOwnershipError):
        await append_run_event(db, **_append_kwargs())

    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_payload_reuses_artifact_secret_safety_gate() -> None:
    db = _db_with_scalar_results(_owned_run())

    with pytest.raises(UnsafeArtifactPayloadError):
        await append_run_event(
            db,
            **_append_kwargs(payload={"runtime": {"api_key": "sk-private-token"}}),
        )

    db.scalar.assert_not_awaited()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_checkpoint_envelope_field_reuses_secret_safety_gate() -> None:
    db = _db_with_scalar_results(_owned_run())

    with pytest.raises(UnsafeArtifactPayloadError):
        await append_run_event(
            db,
            **_append_kwargs(checkpoint_id="sk-private-checkpoint-token"),
        )

    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_emitted_at_must_be_timezone_aware() -> None:
    db = _db_with_scalar_results(_owned_run())

    with pytest.raises(ValueError, match="must include a timezone"):
        await append_run_event(
            db,
            **_append_kwargs(
                emitted_at=datetime(2026, 7, 31, 8, 30, 0),
            ),
        )

    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_emitted_at_is_normalized_to_utc() -> None:
    db = _db_with_scalar_results(_owned_run(), None, 1)
    east_eight = timezone(timedelta(hours=8))

    created = await append_run_event(
        db,
        **_append_kwargs(
            emitted_at=datetime(2026, 7, 31, 16, 30, 0, tzinfo=east_eight)
        ),
    )

    assert created.emitted_at == EMITTED_AT


@pytest.mark.asyncio
async def test_list_is_owned_ordered_after_sequence_and_bounded() -> None:
    first = _event(seq=8, event_id=str(uuid.uuid4()))
    second = _event(seq=9, event_id=str(uuid.uuid4()))
    scalar_result = MagicMock()
    scalar_result.all.return_value = [first, second]
    db = _db_with_scalar_results(_owned_run())
    db.scalars.return_value = scalar_result

    actual = await list_run_events(
        db,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
        after_seq=7,
        limit=25,
    )

    assert actual == [first, second]
    ownership_statement = db.scalar.await_args.args[0]
    assert ownership_statement._for_update_arg is None
    statement = db.scalars.await_args.args[0]
    sql = str(statement)
    assert "deep_research_run_events.run_id" in sql
    assert "deep_research_run_events.workspace_id" in sql
    assert "deep_research_run_events.guest_id" in sql
    assert "deep_research_run_events.seq >" in sql
    assert "ORDER BY deep_research_run_events.seq" in sql
    assert statement._limit_clause.value == 25


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("after_seq", "limit"),
    [(-1, 20), (True, 20), (0, 0), (0, 1001), (0, True)],
)
async def test_list_rejects_unbounded_or_invalid_page_values(
    after_seq: object,
    limit: object,
) -> None:
    db = _db_with_scalar_results(_owned_run())

    with pytest.raises(ValueError):
        await list_run_events(
            db,
            run_id=RUN_ID,
            workspace_id=WORKSPACE_ID,
            guest_id=GUEST_ID,
            after_seq=after_seq,
            limit=limit,
        )

    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_fails_closed_on_unsafe_stored_event() -> None:
    unsafe = _event(payload={"authorization": "Bearer very-private-token"})
    scalar_result = MagicMock()
    scalar_result.all.return_value = [unsafe]
    db = _db_with_scalar_results(_owned_run())
    db.scalars.return_value = scalar_result

    with pytest.raises(DeepResearchEventIntegrityError):
        await list_run_events(
            db,
            run_id=RUN_ID,
            workspace_id=WORKSPACE_ID,
            guest_id=GUEST_ID,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"event_id": "not-a-uuid"},
        {"cycle": -1},
        {"plan_version": -1},
        {"corpus_version": -1},
        {"report_version": 0},
    ],
)
async def test_event_id_and_optional_versions_are_validated(
    override: dict[str, object],
) -> None:
    db = _db_with_scalar_results(_owned_run())

    with pytest.raises(ValueError):
        await append_run_event(db, **_append_kwargs(**override))

    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_type_is_restricted_to_the_v1_console_contract() -> None:
    db = _db_with_scalar_results(_owned_run())

    with pytest.raises(ValueError, match="unsupported Deep Research event type"):
        await append_run_event(
            db,
            **_append_kwargs(type="arbitrary_internal_debug_event"),
        )

    db.scalar.assert_not_awaited()


def test_orm_declares_immutable_replay_keys_and_indexes() -> None:
    table = DeepResearchRunEvent.__table__
    assert "updated_at" not in table.c
    assert table.c.payload.nullable is False
    assert table.c.cycle.nullable is False
    assert table.c.plan_version.nullable is False
    assert table.c.corpus_version.nullable is False
    assert table.c.report_version.nullable is True
    assert {
        constraint.name
        for constraint in table.constraints
        if constraint.name is not None
    } >= {
        "uq_deep_research_run_events_run_seq",
        "uq_deep_research_run_events_run_event_id",
        "ck_deep_research_run_events_schema_version",
    }
    assert {index.name for index in table.indexes} >= {
        "ix_deep_research_run_events_run_seq",
        "ix_deep_research_run_events_owner_seq",
    }


def test_v1_event_type_contract_is_exact() -> None:
    assert DEEP_RESEARCH_EVENT_TYPES == {
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


@pytest.mark.parametrize("event_type", sorted(DEEP_RESEARCH_EVENT_TYPES))
def test_every_v1_event_payload_matches_frontend_contract(event_type: str) -> None:
    payload = VALID_PAYLOADS[event_type]

    assert validate_run_event_payload(event_type, payload) == payload


@pytest.mark.parametrize("event_type", sorted(DEEP_RESEARCH_EVENT_TYPES))
def test_every_v1_event_rejects_a_missing_required_top_level_field(
    event_type: str,
) -> None:
    payload = dict(VALID_PAYLOADS[event_type])
    payload.pop(next(iter(payload)))

    with pytest.raises(DeepResearchEventPayloadError):
        validate_run_event_payload(event_type, payload)


def test_route_selected_cannot_persist_an_untyped_empty_payload() -> None:
    with pytest.raises(DeepResearchEventPayloadError):
        validate_run_event_payload("route_selected", {})


def test_v1_payloads_reject_unknown_fields() -> None:
    payload = {**VALID_PAYLOADS["protocol_error"], "debug_token": "public"}

    with pytest.raises(DeepResearchEventPayloadError):
        validate_run_event_payload("protocol_error", payload)
