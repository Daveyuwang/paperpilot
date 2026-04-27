"""Helper for recording workflow run state to Postgres."""
from __future__ import annotations

import structlog
from datetime import datetime
from typing import Any

from app.db.postgres import AsyncSessionLocal
from app.models.orm import WorkflowRun, WorkflowRunStatus, WorkflowRunType

logger = structlog.get_logger()


async def create_workflow_run(
    *,
    workspace_id: str,
    guest_id: str,
    run_type: WorkflowRunType,
    input_payload: dict | None = None,
) -> str:
    run = WorkflowRun(
        workspace_id=workspace_id,
        guest_id=guest_id,
        run_type=run_type,
        status=WorkflowRunStatus.running,
        input_payload=input_payload,
        stages_completed=[],
    )
    async with AsyncSessionLocal() as db:
        db.add(run)
        await db.commit()
        await db.refresh(run)
    logger.info("workflow_run_created", run_id=run.id, run_type=run_type.value)
    return run.id


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
        run.completed_at = datetime.utcnow()
        run.updated_at = datetime.utcnow()
        await db.commit()
    logger.info("workflow_run_completed", run_id=run_id, status=status.value)
