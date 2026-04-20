import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.guest import require_guest_id
from app.db.postgres import get_db
from app.models.orm import Workspace
from app.models.schemas import WorkspaceOut, WorkspaceCreate, WorkspaceUpdate

logger = structlog.get_logger()
router = APIRouter()


@router.post("/", response_model=WorkspaceOut, status_code=201)
async def create_workspace(
    body: WorkspaceCreate,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    ws = Workspace(guest_id=guest_id, title=body.title, objective=body.objective)
    db.add(ws)
    await db.commit()
    await db.refresh(ws)
    logger.info("workspace_created", workspace_id=ws.id, guest_id=guest_id)
    return WorkspaceOut.model_validate(ws)


@router.get("/", response_model=list[WorkspaceOut])
async def list_workspaces(
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Workspace)
        .where(Workspace.guest_id == guest_id)
        .order_by(Workspace.updated_at.desc())
    )
    return [WorkspaceOut.model_validate(w) for w in result.scalars()]


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(
    workspace_id: str,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_owned_workspace(db, workspace_id, guest_id)
    return WorkspaceOut.model_validate(ws)


@router.put("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    workspace_id: str,
    body: WorkspaceUpdate,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_owned_workspace(db, workspace_id, guest_id)
    if body.title is not None:
        ws.title = body.title
    if body.objective is not None:
        ws.objective = body.objective
    await db.commit()
    await db.refresh(ws)
    return WorkspaceOut.model_validate(ws)


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: str,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_owned_workspace(db, workspace_id, guest_id)
    await db.delete(ws)
    await db.commit()


async def _get_owned_workspace(db: AsyncSession, workspace_id: str, guest_id: str) -> Workspace:
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.guest_id == guest_id)
    )
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return ws
