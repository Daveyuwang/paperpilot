"""
Workspace context and navigation tools for the Console agent.
"""
from __future__ import annotations

from langchain_core.tools import tool


@tool
async def get_workspace_overview(workspace_id: str) -> dict:
    """Get a snapshot of the current workspace state: paper count, source status, and active paper.

    Use this tool when the user asks 'where am I', 'what's the status', 'give me an overview',
    or when you need to orient yourself about the workspace before taking action.

    Args:
        workspace_id: The workspace ID.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select, func
    from app.models.orm import Workspace, Paper, PaperStatus
    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        workspace = await db.get(Workspace, workspace_id)
        if not workspace:
            return {"error": f"Workspace {workspace_id} not found."}

        paper_count_result = await db.execute(
            select(func.count(Paper.id))
            .where(Paper.workspace_id == workspace_id)
        )
        paper_count = paper_count_result.scalar() or 0

        ready_count_result = await db.execute(
            select(func.count(Paper.id))
            .where(Paper.workspace_id == workspace_id, Paper.status == PaperStatus.ready)
        )
        ready_count = ready_count_result.scalar() or 0

        papers_result = await db.execute(
            select(Paper)
            .where(Paper.workspace_id == workspace_id, Paper.status == PaperStatus.ready)
            .order_by(Paper.created_at.desc())
            .limit(5)
        )
        recent_papers = [
            {"id": p.id, "title": p.title or p.filename}
            for p in papers_result.scalars()
        ]

    return {
        "workspace_id": workspace_id,
        "title": workspace.title,
        "objective": workspace.objective,
        "paper_count": paper_count,
        "papers_ready": ready_count,
        "recent_papers": recent_papers,
    }


@tool
async def navigate_to(target: str, paper_id: str = "") -> dict:
    """Tell the frontend to switch to a different view.

    Use this tool when the user asks to open a paper, go to deep research,
    switch to the proposal view, or navigate elsewhere in the UI.

    Args:
        target: Where to navigate. One of: 'reader', 'deep_research', 'proposal', 'workspace'.
        paper_id: Optional paper ID if navigating to a specific paper's reader view.
    """
    valid_targets = {"reader", "deep_research", "proposal", "workspace"}
    if target not in valid_targets:
        return {"error": f"Invalid target '{target}'. Must be one of: {', '.join(valid_targets)}"}

    result: dict = {"action": "navigate", "target": target}
    if paper_id:
        result["paper_id"] = paper_id
    return result
