from datetime import datetime

from fastapi import Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import Paper, Session


def require_guest_id(x_guest_id: str | None = Header(default=None, alias="X-Guest-Id")) -> str:
    guest_id = (x_guest_id or "").strip()
    if not guest_id:
        raise HTTPException(status_code=400, detail="Missing X-Guest-Id header.")
    return guest_id


def require_guest_id_for_download(
    x_guest_id: str | None = Header(default=None, alias="X-Guest-Id"),
    guest_id: str | None = Query(default=None),
) -> str:
    resolved_guest_id = (x_guest_id or guest_id or "").strip()
    if not resolved_guest_id:
        raise HTTPException(status_code=400, detail="Missing guest identifier.")
    return resolved_guest_id


async def get_owned_paper_or_404(db: AsyncSession, paper_id: str, guest_id: str) -> Paper:
    paper = await db.get(Paper, paper_id)
    if not paper or paper.guest_id != guest_id:
        raise HTTPException(status_code=404, detail="Paper not found.")
    return paper


async def get_owned_session_or_404(db: AsyncSession, session_id: str, guest_id: str) -> Session:
    session = await db.get(Session, session_id)
    if not session or session.guest_id != guest_id:
        raise HTTPException(status_code=404, detail="Session not found.")
    session.last_active = datetime.utcnow()
    await db.commit()
    await db.refresh(session)
    return session
