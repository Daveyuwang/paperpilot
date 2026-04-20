import uuid
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.orm import Session, Workspace
from app.db.postgres import get_db
from app.db.redis_client import get_session_state, delete_session_state
from app.api.guest import require_guest_id, get_owned_paper_or_404, get_owned_session_or_404
from app.models.schemas import SessionOut

logger = structlog.get_logger()
router = APIRouter()


class WorkspaceSessionCreate(BaseModel):
    workspace_id: str


@router.post("/workspace/console", response_model=SessionOut)
async def create_workspace_session(
    body: WorkspaceSessionCreate,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a workspace-level console session (no paper)."""
    result = await db.execute(
        select(Workspace).where(Workspace.id == body.workspace_id, Workspace.guest_id == guest_id)
    )
    ws = result.scalar_one_or_none()
    if not ws:
        ws = Workspace(id=body.workspace_id, guest_id=guest_id, title="My Research Workspace")
        db.add(ws)
        await db.flush()

    session = Session(id=str(uuid.uuid4()), guest_id=guest_id, paper_id=None, workspace_id=body.workspace_id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    logger.info("workspace_session_created", session_id=session.id, workspace_id=body.workspace_id, guest_id=guest_id)
    return SessionOut.model_validate(session)


@router.post("/{paper_id}", response_model=SessionOut)
async def create_session(
    paper_id: str,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a new reading session for a paper."""
    await get_owned_paper_or_404(db, paper_id, guest_id)

    session = Session(id=str(uuid.uuid4()), guest_id=guest_id, paper_id=paper_id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    logger.info("session_created", session_id=session.id, paper_id=paper_id, guest_id=guest_id)
    return SessionOut.model_validate(session)


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: str,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    session = await get_owned_session_or_404(db, session_id, guest_id)
    return SessionOut.model_validate(session)


@router.get("/{session_id}/state")
async def get_session_state_endpoint(
    session_id: str,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    """Return the Redis-backed agent state for a session."""
    session = await get_owned_session_or_404(db, session_id, guest_id)
    state = await get_session_state(session_id)
    return state


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    session = await get_owned_session_or_404(db, session_id, guest_id)
    await delete_session_state(session_id)
    await db.delete(session)
    await db.commit()
