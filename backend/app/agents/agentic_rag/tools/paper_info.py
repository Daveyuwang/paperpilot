"""
Paper information tools for the agentic RAG system.
Wraps DB lookups for paper metadata, concept maps, and guided questions.
"""
from __future__ import annotations

from langchain_core.tools import tool


@tool
async def get_paper_metadata(paper_id: str) -> dict:
    """Get metadata for a paper: title, abstract, authors, section headings, and page count.

    Use this tool to orient yourself about a paper before retrieving specific passages,
    or when the user asks about the paper's structure, authors, or high-level content.

    Args:
        paper_id: The paper ID.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.models.orm import Paper
    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        paper = await db.get(Paper, paper_id)
        if not paper:
            return {"error": f"Paper {paper_id} not found."}
        return {
            "paper_id": paper.id,
            "title": paper.title or "",
            "abstract": (paper.abstract or "")[:500],
            "authors": paper.authors or [],
            "section_headers": paper.section_headers or [],
            "page_count": paper.page_count,
        }


@tool
async def get_concept_map(paper_id: str) -> dict:
    """Get the concept map for a paper — nodes (concepts) and edges (relationships).

    Use this tool when the user asks about how concepts in the paper relate to each other,
    or wants an overview of the paper's key ideas and their connections.

    Args:
        paper_id: The paper ID.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.models.orm import PaperConceptMap
    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        cm = await db.get(PaperConceptMap, paper_id)
        if not cm or not cm.data:
            return {"paper_id": paper_id, "nodes": [], "edges": [], "note": "No concept map available."}
        return {
            "paper_id": paper_id,
            "nodes": cm.data.get("nodes", [])[:30],
            "edges": cm.data.get("edges", [])[:50],
        }


@tool
async def get_guided_questions(paper_id: str, session_id: str) -> dict:
    """Get the guided reading questions for a paper and the user's progress through them.

    Use this tool when the user asks what to read next, what questions remain,
    or wants to see their reading progress through the paper.

    Args:
        paper_id: The paper ID.
        session_id: The session ID (to check which questions have been covered).
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select
    from app.models.orm import GuideQuestion
    from app.db.redis_client import get_session_state
    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        result = await db.execute(
            select(GuideQuestion)
            .where(GuideQuestion.paper_id == paper_id)
            .order_by(GuideQuestion.order_index)
        )
        questions = [
            {
                "id": q.id,
                "question": q.question,
                "stage": q.stage.value,
            }
            for q in result.scalars()
        ]

    session_state = await get_session_state(session_id)
    covered_ids = set(session_state.get("covered_question_ids", []))

    return {
        "paper_id": paper_id,
        "total": len(questions),
        "covered": len(covered_ids),
        "questions": [
            {**q, "covered": q["id"] in covered_ids}
            for q in questions
        ],
    }
