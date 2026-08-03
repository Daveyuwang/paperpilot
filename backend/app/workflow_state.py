"""Helper for recording workflow run state to Postgres."""
from __future__ import annotations

import structlog
from datetime import datetime
from typing import Any

from app.db.postgres import AsyncSessionLocal
from sqlalchemy import select

from app.models.orm import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowRunType,
    Workspace,
)

logger = structlog.get_logger()


async def create_workflow_run(
    *,
    run_id: str | None = None,
    workspace_id: str,
    guest_id: str,
    run_type: WorkflowRunType,
    input_payload: dict | None = None,
    artifacts: dict[str, Any] | None = None,
) -> str:
    run_values: dict[str, Any] = {
        "workspace_id": workspace_id,
        "guest_id": guest_id,
        "run_type": run_type,
        "status": WorkflowRunStatus.running,
        "input_payload": input_payload,
        "stages_completed": [],
        "artifacts": artifacts,
    }
    if run_id is not None:
        run_values["id"] = run_id
    run = WorkflowRun(
        **run_values,
    )
    async with AsyncSessionLocal() as db:
        db.add(run)
        await db.commit()
        await db.refresh(run)
    logger.info("workflow_run_created", run_id=run.id, run_type=run_type.value)
    return run.id


async def get_owned_workspace(
    *,
    workspace_id: str,
    guest_id: str,
) -> Workspace | None:
    """Return a workspace only inside the caller's exact ownership scope."""

    async with AsyncSessionLocal() as db:
        return await db.scalar(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.guest_id == guest_id,
            )
        )


async def get_owned_workflow_run(
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    run_type: WorkflowRunType | None = None,
) -> WorkflowRun | None:
    """Return a run only after workspace, guest, and optional type match."""

    statement = select(WorkflowRun).where(
        WorkflowRun.id == run_id,
        WorkflowRun.workspace_id == workspace_id,
        WorkflowRun.guest_id == guest_id,
    )
    if run_type is not None:
        statement = statement.where(WorkflowRun.run_type == run_type)
    async with AsyncSessionLocal() as db:
        return await db.scalar(statement)


async def mark_workflow_run_running(
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
) -> WorkflowRun | None:
    """Mark an owned non-terminal run active immediately before resuming it."""

    async with AsyncSessionLocal() as db:
        run = await db.scalar(
            select(WorkflowRun).where(
                WorkflowRun.id == run_id,
                WorkflowRun.workspace_id == workspace_id,
                WorkflowRun.guest_id == guest_id,
                WorkflowRun.run_type == WorkflowRunType.deep_research,
            )
        )
        if run is None:
            return None
        if run.status in {
            WorkflowRunStatus.completed,
            WorkflowRunStatus.incomplete,
        }:
            return run
        run.status = WorkflowRunStatus.running
        run.error = None
        run.completed_at = None
        run.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(run)
        return run


async def stop_owned_deep_research_run(
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    status: WorkflowRunStatus,
    error: dict[str, Any],
    artifacts: dict[str, Any] | None = None,
) -> WorkflowRun | None:
    """Atomically stop a non-terminal run without clobbering a graph terminal."""

    if status not in {WorkflowRunStatus.failed, WorkflowRunStatus.interrupted}:
        raise ValueError("execution stop status must be failed or interrupted")
    async with AsyncSessionLocal() as db:
        async with db.begin():
            run = await db.scalar(
                select(WorkflowRun)
                .where(
                    WorkflowRun.id == run_id,
                    WorkflowRun.workspace_id == workspace_id,
                    WorkflowRun.guest_id == guest_id,
                    WorkflowRun.run_type == WorkflowRunType.deep_research,
                )
                .with_for_update()
            )
            if run is None or run.status in {
                WorkflowRunStatus.completed,
                WorkflowRunStatus.incomplete,
            }:
                return run
            now = datetime.utcnow()
            run.status = status
            run.error = error
            if artifacts:
                public_artifacts = dict(run.artifacts or {})
                public_artifacts.update(artifacts)
                run.artifacts = public_artifacts
            run.completed_at = (
                None if status == WorkflowRunStatus.interrupted else now
            )
            run.updated_at = now
            await db.flush()
            return run


async def update_workflow_stage(
    run_id: str,
    *,
    stage: str,
    artifacts: dict[str, Any] | None = None,
):
    async with AsyncSessionLocal() as db:
        run = await db.get(WorkflowRun, run_id)
        if not run:
            return
        run.current_stage = stage
        completed = list(run.stages_completed or [])
        if stage not in completed:
            completed.append(stage)
        run.stages_completed = completed
        if artifacts:
            existing = dict(run.artifacts or {})
            existing.update(artifacts)
            run.artifacts = existing
        run.updated_at = datetime.utcnow()
        await db.commit()


async def complete_workflow_run(
    run_id: str,
    *,
    status: WorkflowRunStatus = WorkflowRunStatus.completed,
    error: dict | None = None,
    token_usage: dict | None = None,
    artifacts: dict[str, Any] | None = None,
):
    async with AsyncSessionLocal() as db:
        run = await db.get(WorkflowRun, run_id)
        if not run:
            return
        run.status = status
        if error:
            run.error = error
        if token_usage:
            run.token_usage = token_usage
        if artifacts:
            existing = dict(run.artifacts or {})
            existing.update(artifacts)
            run.artifacts = existing
        now = datetime.utcnow()
        run.completed_at = (
            None if status == WorkflowRunStatus.interrupted else now
        )
        run.updated_at = now
        await db.commit()
    logger.info("workflow_run_completed", run_id=run_id, status=status.value)
