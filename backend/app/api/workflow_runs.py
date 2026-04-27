import structlog
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.guest import require_guest_id
from app.db.postgres import AsyncSessionLocal
from app.models.orm import WorkflowRun, WorkflowRunStatus
from app.models.schemas import WorkflowRunOut

logger = structlog.get_logger()
router = APIRouter()


@router.get("/", response_model=list[WorkflowRunOut])
async def list_workflow_runs(
    workspace_id: str,
    guest_id: str = Depends(require_guest_id),
):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(WorkflowRun)
            .where(WorkflowRun.workspace_id == workspace_id)
            .where(WorkflowRun.guest_id == guest_id)
            .order_by(WorkflowRun.created_at.desc())
            .limit(50)
        )
        runs = result.scalars().all()
    return runs


@router.get("/{run_id}", response_model=WorkflowRunOut)
async def get_workflow_run(
    run_id: str,
    guest_id: str = Depends(require_guest_id),
):
    async with AsyncSessionLocal() as db:
        run = await db.get(WorkflowRun, run_id)
        if not run or run.guest_id != guest_id:
            raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.post("/{run_id}/resume")
async def resume_workflow_run(
    run_id: str,
    guest_id: str = Depends(require_guest_id),
):
    async with AsyncSessionLocal() as db:
        run = await db.get(WorkflowRun, run_id)
        if not run or run.guest_id != guest_id:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.status != WorkflowRunStatus.interrupted:
            raise HTTPException(status_code=400, detail="Only interrupted runs can be resumed")
        run.status = WorkflowRunStatus.running
        run.updated_at = datetime.utcnow()
        await db.commit()
    return {"status": "resumed", "run_id": run_id}
