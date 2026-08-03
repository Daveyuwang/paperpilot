from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.deep_research import artifacts as artifact_store
from app.deep_research.artifacts import (
    ArtifactLineageError,
    ArtifactOwnershipError,
    DeepResearchArtifactConflictError,
    UnsafeArtifactPayloadError,
    canonical_payload_hash,
    get_artifact_snapshot,
    validate_artifact_payload,
    write_artifact_version,
)
from app.models.orm import (
    DeepResearchArtifactKind,
    DeepResearchArtifactVersion,
    WorkflowRunType,
)


RUN_ID = "96eff9be-5338-44dd-82cc-b22d61046619"
WORKSPACE_ID = "16c7fc3c-6fe2-40fe-9b84-98b980479c42"
GUEST_ID = "guest-artifact-tests"


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
    db.flush = AsyncMock()
    db.add = MagicMock()
    db.begin_nested = MagicMock(return_value=_NestedTransaction())
    db.scalars = AsyncMock()
    return db


def _write_kwargs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "workspace_id": WORKSPACE_ID,
        "guest_id": GUEST_ID,
        "artifact_kind": DeepResearchArtifactKind.plan,
        "logical_artifact_id": "active-plan",
        "version_number": 1,
        "plan_version": 1,
        "controller_cycle": 0,
        "schema_version": 1,
        "write_key": "plan:1",
        "payload": {
            "sub_questions": [{"id": "sq-1", "question": "What changed?"}],
            "plan_version": 1,
        },
        "source_checkpoint_id": "checkpoint-1",
    }
    values.update(overrides)
    return values


def _artifact(
    *,
    artifact_id: str,
    logical_id: str,
    version: int,
    cycle: int,
    plan_version: int,
    created_at: datetime,
    kind: DeepResearchArtifactKind = DeepResearchArtifactKind.plan,
    parent_version_id: str | None = None,
) -> DeepResearchArtifactVersion:
    payload = {
        "logical_id": logical_id,
        "version": version,
        "cycle": cycle,
    }
    return DeepResearchArtifactVersion(
        id=artifact_id,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
        artifact_kind=kind,
        logical_artifact_id=logical_id,
        version_number=version,
        plan_version=plan_version,
        controller_cycle=cycle,
        schema_version=1,
        parent_version_id=parent_version_id,
        source_checkpoint_id=f"checkpoint-{cycle}",
        content_hash=canonical_payload_hash(payload),
        write_key=f"{kind.value}:{logical_id}:{version}",
        payload=payload,
        created_at=created_at,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": "sk-secret-value"},
        {"nested": {"apiKey": "sk-secret-value"}},
        {"authorization": "Bearer secret-token"},
        {"proxy_authorization": "Basic dXNlcjpwYXNz"},
        {"cookie": "session=private-value"},
        {"set_cookie": "session=private-value"},
        {"webhook_secret": "private-value"},
        {"runtime": {"base_url": "https://private-llm.invalid/v1"}},
        {"note": "Bearer abcdefghijklmnop"},
        {"note": "api_key=abcdefghijk"},
        {"credential": "xai-1234567890abcdef"},
        {"credential": "https://user:password@private.invalid/v1"},
    ],
)
def test_artifact_payload_rejects_credentials_and_private_runtime_config(
    payload: dict[str, object],
) -> None:
    with pytest.raises(UnsafeArtifactPayloadError):
        validate_artifact_payload(payload)
    with pytest.raises(UnsafeArtifactPayloadError):
        canonical_payload_hash(payload)


def test_canonical_payload_hash_is_order_independent() -> None:
    first = {
        "plan_version": 2,
        "sub_questions": [{"question": "Q", "id": "sq-1"}],
    }
    reordered = {
        "sub_questions": [{"id": "sq-1", "question": "Q"}],
        "plan_version": 2,
    }

    assert canonical_payload_hash(first) == canonical_payload_hash(reordered)


def test_artifact_payload_allows_public_usage_and_source_metadata() -> None:
    payload = {
        "token_usage": {"input_tokens": 123, "output_tokens": 45},
        "source": {"url": "https://example.test/paper", "title": "Public source"},
        "issues": [{"id": "issue-1", "reason_code": "insufficient_coverage"}],
    }

    validate_artifact_payload(payload)
    assert len(canonical_payload_hash(payload)) == 64


@pytest.mark.asyncio
async def test_artifact_write_is_idempotent_for_the_exact_same_operation() -> None:
    db = _db_with_scalar_results(_owned_run(), None)

    first = await write_artifact_version(db, **_write_kwargs())
    db.scalar.side_effect = [_owned_run(), first]
    replay = await write_artifact_version(db, **_write_kwargs())

    assert replay is first
    assert db.add.call_count == 1
    assert db.flush.await_count == 1
    assert first.source_checkpoint_id == "checkpoint-1"
    assert first.content_hash == canonical_payload_hash(first.payload)


@pytest.mark.asyncio
async def test_write_key_reuse_with_different_payload_conflicts() -> None:
    existing_db = _db_with_scalar_results(_owned_run(), None)
    existing = await write_artifact_version(existing_db, **_write_kwargs())
    replay_db = _db_with_scalar_results(_owned_run(), existing)

    with pytest.raises(DeepResearchArtifactConflictError):
        await write_artifact_version(
            replay_db,
            **_write_kwargs(payload={"plan_version": 1, "sub_questions": []}),
        )

    replay_db.add.assert_not_called()


@pytest.mark.parametrize(
    "metadata_override",
    [
        {"artifact_kind": DeepResearchArtifactKind.sub_report},
        {"logical_artifact_id": "another-plan"},
        {"plan_version": 2},
        {"controller_cycle": 1},
        {"schema_version": 2},
        {"source_checkpoint_id": "checkpoint-2"},
    ],
)
@pytest.mark.asyncio
async def test_write_key_reuse_cannot_alias_different_version_metadata(
    metadata_override: dict[str, object],
) -> None:
    existing_db = _db_with_scalar_results(_owned_run(), None)
    existing = await write_artifact_version(existing_db, **_write_kwargs())
    replay_db = _db_with_scalar_results(_owned_run(), existing)

    with pytest.raises(DeepResearchArtifactConflictError):
        await write_artifact_version(
            replay_db,
            **_write_kwargs(**metadata_override),
        )

    replay_db.add.assert_not_called()


@pytest.mark.asyncio
async def test_write_key_cannot_alias_a_different_version_or_parent() -> None:
    payload = _write_kwargs()["payload"]
    assert isinstance(payload, dict)
    existing = DeepResearchArtifactVersion(
        id=str(uuid.uuid4()),
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
        artifact_kind=DeepResearchArtifactKind.plan,
        logical_artifact_id="active-plan",
        version_number=1,
        plan_version=1,
        controller_cycle=0,
        schema_version=1,
        parent_version_id=None,
        source_checkpoint_id="checkpoint-1",
        content_hash=canonical_payload_hash(payload),
        write_key="plan:1",
        payload=payload,
        created_at=datetime.utcnow(),
    )
    replay_db = _db_with_scalar_results(_owned_run(), existing)

    with pytest.raises(DeepResearchArtifactConflictError):
        await write_artifact_version(
            replay_db,
            **_write_kwargs(
                version_number=2,
                plan_version=2,
                controller_cycle=1,
                parent_version_id="e87a51b5-fc86-429a-825a-47507e9d5573",
                source_checkpoint_id="checkpoint-2",
            ),
        )


@pytest.mark.asyncio
async def test_unsafe_payload_is_rejected_before_any_database_access() -> None:
    db = _db_with_scalar_results(_owned_run(), None)

    with pytest.raises(UnsafeArtifactPayloadError):
        await write_artifact_version(
            db,
            **_write_kwargs(
                payload={"events": [{"detail": {"api_key": "sk-secret-value"}}]}
            ),
        )

    db.scalar.assert_not_awaited()
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_idempotent_read_detects_corrupted_stored_payload() -> None:
    existing_db = _db_with_scalar_results(_owned_run(), None)
    existing = await write_artifact_version(existing_db, **_write_kwargs())
    existing.payload = {"tampered": True}
    replay_db = _db_with_scalar_results(_owned_run(), existing)

    with pytest.raises(artifact_store.ArtifactIntegrityError):
        await write_artifact_version(replay_db, **_write_kwargs())


@pytest.mark.asyncio
async def test_concurrent_logical_version_conflict_fails_closed() -> None:
    db = _db_with_scalar_results(_owned_run(), None, None)
    db.flush.side_effect = IntegrityError(
        "duplicate logical version",
        params={},
        orig=RuntimeError("unique violation"),
    )

    with pytest.raises(DeepResearchArtifactConflictError):
        await write_artifact_version(db, **_write_kwargs())


@pytest.mark.asyncio
async def test_concurrent_same_write_replays_the_existing_artifact() -> None:
    existing_db = _db_with_scalar_results(_owned_run(), None)
    existing = await write_artifact_version(existing_db, **_write_kwargs())
    raced_db = _db_with_scalar_results(_owned_run(), None, existing)
    raced_db.flush.side_effect = IntegrityError(
        "concurrent same write",
        params={},
        orig=RuntimeError("unique violation"),
    )

    actual = await write_artifact_version(raced_db, **_write_kwargs())

    assert actual is existing


@pytest.mark.asyncio
async def test_artifact_write_requires_exact_run_ownership_scope() -> None:
    db = _db_with_scalar_results(None)

    with pytest.raises(ArtifactOwnershipError):
        await write_artifact_version(db, **_write_kwargs())

    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reader",
    [artifact_store.list_artifact_versions, artifact_store.get_artifact_snapshot],
)
async def test_artifact_reader_requires_exact_run_ownership_scope(reader) -> None:
    db = _db_with_scalar_results(None)

    with pytest.raises(ArtifactOwnershipError):
        await reader(
            db,
            run_id=RUN_ID,
            workspace_id=WORKSPACE_ID,
            guest_id="wrong-owner",
        )

    db.scalars.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_key_lookup_is_scoped_by_run_workspace_and_guest() -> None:
    db = _db_with_scalar_results(_owned_run(), None)

    await write_artifact_version(db, **_write_kwargs())

    statement = db.scalar.await_args_list[1].args[0]
    sql = str(statement)
    assert "deep_research_artifact_versions.run_id" in sql
    assert "deep_research_artifact_versions.workspace_id" in sql
    assert "deep_research_artifact_versions.guest_id" in sql
    assert "deep_research_artifact_versions.write_key" in sql


@pytest.mark.asyncio
async def test_artifact_parent_lineage_must_be_monotonic_and_same_identity() -> None:
    now = datetime.utcnow()
    parent = _artifact(
        artifact_id=str(uuid.uuid4()),
        logical_id="active-plan",
        version=2,
        cycle=2,
        plan_version=2,
        created_at=now,
    )
    db = _db_with_scalar_results(_owned_run(), None, parent)

    with pytest.raises(ArtifactLineageError):
        await write_artifact_version(
            db,
            **_write_kwargs(
                version_number=2,
                plan_version=2,
                controller_cycle=2,
                parent_version_id=parent.id,
            ),
        )

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_version_one_cannot_name_a_parent() -> None:
    db = _db_with_scalar_results(_owned_run(), None)

    with pytest.raises(ArtifactLineageError):
        await write_artifact_version(
            db,
            **_write_kwargs(
                parent_version_id="e87a51b5-fc86-429a-825a-47507e9d5573"
            ),
        )

    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parent_override",
    [
        {"artifact_kind": DeepResearchArtifactKind.sub_report},
        {"logical_artifact_id": "different-plan"},
    ],
)
async def test_parent_must_match_kind_and_logical_identity(
    parent_override: dict[str, object],
) -> None:
    parent = _artifact(
        artifact_id=str(uuid.uuid4()),
        logical_id="active-plan",
        version=1,
        cycle=0,
        plan_version=1,
        created_at=datetime.utcnow(),
    )
    for field, value in parent_override.items():
        setattr(parent, field, value)
    db = _db_with_scalar_results(_owned_run(), None, parent)

    with pytest.raises(ArtifactLineageError):
        await write_artifact_version(
            db,
            **_write_kwargs(
                version_number=2,
                plan_version=2,
                controller_cycle=1,
                parent_version_id=parent.id,
                source_checkpoint_id="checkpoint-2",
            ),
        )


@pytest.mark.asyncio
async def test_version_after_first_requires_exact_immediate_parent() -> None:
    parent = _artifact(
        artifact_id=str(uuid.uuid4()),
        logical_id="active-plan",
        version=1,
        cycle=0,
        plan_version=1,
        created_at=datetime.utcnow(),
    )

    missing_parent_db = _db_with_scalar_results(_owned_run(), None)
    with pytest.raises(ArtifactLineageError):
        await write_artifact_version(
            missing_parent_db,
            **_write_kwargs(version_number=2, source_checkpoint_id="checkpoint-2"),
        )

    skipped_parent_db = _db_with_scalar_results(_owned_run(), None, parent)
    with pytest.raises(ArtifactLineageError):
        await write_artifact_version(
            skipped_parent_db,
            **_write_kwargs(
                version_number=3,
                plan_version=3,
                controller_cycle=2,
                parent_version_id=parent.id,
                source_checkpoint_id="checkpoint-3",
            ),
        )


@pytest.mark.asyncio
async def test_immediate_parent_records_version_and_checkpoint_lineage() -> None:
    parent = _artifact(
        artifact_id=str(uuid.uuid4()),
        logical_id="active-plan",
        version=1,
        cycle=0,
        plan_version=1,
        created_at=datetime.utcnow(),
    )
    db = _db_with_scalar_results(_owned_run(), None, parent)

    child = await write_artifact_version(
        db,
        **_write_kwargs(
            version_number=2,
            plan_version=2,
            controller_cycle=1,
            parent_version_id=parent.id,
            source_checkpoint_id="checkpoint-2",
        ),
    )

    assert child.parent_version_id == parent.id
    assert child.source_checkpoint_id == "checkpoint-2"
    assert child.version_number == parent.version_number + 1


@pytest.mark.asyncio
async def test_list_includes_cycle_zero_and_verifies_stored_hash() -> None:
    artifact = _artifact(
        artifact_id=str(uuid.uuid4()),
        logical_id="active-plan",
        version=1,
        cycle=0,
        plan_version=1,
        created_at=datetime.utcnow(),
    )
    scalar_result = MagicMock()
    scalar_result.all.return_value = [artifact]
    db = _db_with_scalar_results(_owned_run())
    db.scalars.return_value = scalar_result

    actual = await artifact_store.list_artifact_versions(
        db,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
        through_controller_cycle=0,
    )

    assert actual == [artifact]
    statement = db.scalars.await_args.args[0]
    sql = str(statement)
    assert "controller_cycle <=" in sql
    assert "ORDER BY deep_research_artifact_versions.controller_cycle" in sql
    assert "deep_research_artifact_versions.created_at" in sql
    assert "deep_research_artifact_versions.id" in sql


@pytest.mark.asyncio
async def test_list_rejects_a_corrupted_stored_artifact() -> None:
    artifact = _artifact(
        artifact_id=str(uuid.uuid4()),
        logical_id="active-plan",
        version=1,
        cycle=0,
        plan_version=1,
        created_at=datetime.utcnow(),
    )
    artifact.payload = {"tampered": True}
    scalar_result = MagicMock()
    scalar_result.all.return_value = [artifact]
    db = _db_with_scalar_results(_owned_run())
    db.scalars.return_value = scalar_result

    with pytest.raises(artifact_store.ArtifactIntegrityError):
        await artifact_store.list_artifact_versions(
            db,
            run_id=RUN_ID,
            workspace_id=WORKSPACE_ID,
            guest_id=GUEST_ID,
        )


@pytest.mark.asyncio
async def test_snapshot_returns_latest_version_with_checkpoint_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.utcnow()
    plan_v1 = _artifact(
        artifact_id=str(uuid.uuid4()),
        logical_id="active-plan",
        version=1,
        cycle=0,
        plan_version=1,
        created_at=now,
    )
    plan_v2 = _artifact(
        artifact_id=str(uuid.uuid4()),
        logical_id="active-plan",
        version=2,
        cycle=1,
        plan_version=2,
        created_at=now + timedelta(seconds=1),
        parent_version_id=plan_v1.id,
    )
    report = _artifact(
        artifact_id=str(uuid.uuid4()),
        logical_id="sq-1",
        version=1,
        cycle=1,
        plan_version=2,
        created_at=now + timedelta(seconds=2),
        kind=DeepResearchArtifactKind.sub_report,
    )
    observed: dict[str, object] = {}

    async def fake_list(_db, **kwargs):
        observed.update(kwargs)
        return [report, plan_v2, plan_v1]

    monkeypatch.setattr(artifact_store, "list_artifact_versions", fake_list)
    snapshot = await get_artifact_snapshot(
        object(),
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
        through_controller_cycle=1,
    )

    assert snapshot == [plan_v2, report]
    assert plan_v2.parent_version_id == plan_v1.id
    assert plan_v2.source_checkpoint_id == "checkpoint-1"
    assert observed == {
        "run_id": RUN_ID,
        "workspace_id": WORKSPACE_ID,
        "guest_id": GUEST_ID,
        "through_controller_cycle": 1,
    }
