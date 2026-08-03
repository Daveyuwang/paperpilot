from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest

from app import workflow_state
from app.deep_research.context import DEEP_RESEARCH_GRAPH_VERSION
from app.models.orm import WorkflowRunStatus, WorkflowRunType


class _SessionContext:
    def __init__(self, db: MagicMock):
        self.db = db

    async def __aenter__(self) -> MagicMock:
        return self.db

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        return False


def _install_session(monkeypatch: pytest.MonkeyPatch, db: MagicMock) -> None:
    monkeypatch.setattr(
        workflow_state,
        "AsyncSessionLocal",
        lambda: _SessionContext(db),
    )


def _db() -> MagicMock:
    db = MagicMock()
    db.scalar = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.mark.asyncio
async def test_create_workflow_run_preserves_explicit_full_uuid_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = str(uuid.uuid4())
    db = _db()
    _install_session(monkeypatch, db)

    returned = await workflow_state.create_workflow_run(
        run_id=run_id,
        workspace_id=str(uuid.uuid4()),
        guest_id="guest-workflow-identity",
        run_type=WorkflowRunType.deep_research,
        input_payload={"topic": "one canonical run identity"},
        artifacts={
            "graph_version": DEEP_RESEARCH_GRAPH_VERSION,
            "checkpoint_backend": "memory",
        },
    )

    created = db.add.call_args.args[0]
    assert returned == created.id == run_id
    assert uuid.UUID(returned).version == 4
    assert created.artifacts == {
        "graph_version": DEEP_RESEARCH_GRAPH_VERSION,
        "checkpoint_backend": "memory",
    }
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(created)


@pytest.mark.asyncio
async def test_owned_run_lookup_scopes_id_workspace_guest_and_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    db = _db()
    db.scalar.return_value = sentinel
    _install_session(monkeypatch, db)
    run_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    actual = await workflow_state.get_owned_workflow_run(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id="guest-owner",
        run_type=WorkflowRunType.deep_research,
    )

    assert actual is sentinel
    sql = str(db.scalar.await_args.args[0])
    assert "workflow_runs.id" in sql
    assert "workflow_runs.workspace_id" in sql
    assert "workflow_runs.guest_id" in sql
    assert "workflow_runs.run_type" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_status",
    [
        WorkflowRunStatus.completed,
        WorkflowRunStatus.incomplete,
    ],
)
async def test_mark_running_is_idempotent_for_every_terminal_status(
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: WorkflowRunStatus,
) -> None:
    completed_at = datetime(2026, 7, 31, 12, 0, 0)
    run = MagicMock(
        status=terminal_status,
        error={"code": "terminal"},
        completed_at=completed_at,
    )
    db = _db()
    db.scalar.return_value = run
    _install_session(monkeypatch, db)

    actual = await workflow_state.mark_workflow_run_running(
        run_id=str(uuid.uuid4()),
        workspace_id=str(uuid.uuid4()),
        guest_id="guest-owner",
    )

    assert actual is run
    assert run.status == terminal_status
    assert run.error == {"code": "terminal"}
    assert run.completed_at == completed_at
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_resumable_run_running_clears_stale_terminal_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MagicMock(
        # Failed-run admission depends on a compatible checkpoint with remaining
        # nodes and is covered at the resume API boundary. This state helper is
        # unconditional only for an already-admitted interrupted run.
        status=WorkflowRunStatus.interrupted,
        error={"code": "client_disconnected"},
        completed_at=datetime(2026, 7, 31, 12, 0, 0),
    )
    db = _db()
    db.scalar.return_value = run
    _install_session(monkeypatch, db)

    actual = await workflow_state.mark_workflow_run_running(
        run_id=str(uuid.uuid4()),
        workspace_id=str(uuid.uuid4()),
        guest_id="guest-owner",
    )

    assert actual is run
    assert run.status == WorkflowRunStatus.running
    assert run.error is None
    assert run.completed_at is None
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(run)
