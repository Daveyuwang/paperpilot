"""
WebSocket integration for the agentic RAG system.
Replaces run_agent_turn and run_console_turn with LangGraph astream_events.
Maps LangGraph events to the existing WebSocket message format.
"""
from __future__ import annotations

import json
import structlog
from typing import AsyncGenerator

from langchain_core.messages import HumanMessage

from app.agents.agentic_rag.graphs.main import main_graph
from app.db.redis_client import get_session_state, set_session_state

logger = structlog.get_logger()


async def run_agentic_turn(
    session_id: str,
    question: str,
    paper_id: str | None = None,
    workspace_id: str | None = None,
    guest_id: str = "",
    context: dict | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Run one agentic turn using the LangGraph main graph.
    Yields WebSocket message dicts compatible with the existing frontend.
    """
    ctx = context or {}

    # Load paper metadata if paper session
    paper_title = ""
    paper_abstract = ""
    if paper_id:
        paper_title, paper_abstract = await _load_paper_meta(paper_id)

    # Load session state
    session_state = await get_session_state(session_id)
    session_summary = session_state.get("session_summary", "")

    # Build workspace context for console
    workspace_title = ""
    paper_count = 0
    active_paper_name = "None"
    if workspace_id:
        workspace_title, paper_count = await _load_workspace_meta(workspace_id)
    if ctx.get("active_paper_id"):
        active_paper_name = await _get_paper_title(ctx["active_paper_id"]) or "Active paper"

    input_state = {
        "messages": [HumanMessage(content=question)],
        "route": "",
        # Paper QA context
        "paper_id": paper_id or "",
        "session_id": session_id,
        "guest_id": guest_id,
        "paper_title": paper_title,
        "paper_abstract": paper_abstract,
        "session_summary": session_summary,
        # Console context
        "workspace_id": workspace_id or "",
        "active_paper_id": ctx.get("active_paper_id", ""),
        "workspace_title": workspace_title,
        "paper_count": paper_count,
        "active_paper_name": active_paper_name,
        "workspace_snapshot": "",
    }

    yield {"type": "status", "content": "Received"}

    try:
        final_state = None
        tool_events_emitted = set()

        async for event in main_graph.astream_events(input_state, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")

            # Tool start → status message
            if kind == "on_tool_start" and name not in tool_events_emitted:
                tool_events_emitted.add(name)
                status_text = _tool_status_message(name)
                if status_text:
                    yield {"type": "status", "content": status_text}

            # Chat model stream → token events
            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    # Only stream tokens from final response nodes
                    tags = event.get("tags", [])
                    if "paper_qa" in name or "console" in name or "direct_response" in name:
                        yield {"type": "token", "content": chunk.content}

            # Chain end → capture final state
            elif kind == "on_chain_end" and name == "LangGraph":
                final_state = event.get("data", {}).get("output", {})

        # Extract answer from final state
        if final_state:
            answer_json = _extract_answer_json(final_state)
            if answer_json:
                yield {"type": "mode_info", "content": {
                    "answer_mode": answer_json.get("answer_mode", "paper_understanding"),
                    "scope_label": answer_json.get("scope_label", ""),
                }}
                yield {"type": "answer_json", "content": answer_json}

                citations = answer_json.pop("_citations", None)
                if citations:
                    yield {"type": "chunk_refs", "content": citations}

            yield {"type": "answer_done", "content": answer_json or ""}

            # Update session
            await _update_session_state(session_id, question, final_state, session_state)

    except Exception as exc:
        logger.exception("agentic_turn_failed", session_id=session_id, error=str(exc))
        yield {"type": "error", "content": str(exc)}


def _tool_status_message(tool_name: str) -> str:
    """Map tool names to user-facing status messages."""
    return {
        "retrieve_from_paper": "Retrieving passages from paper…",
        "search_workspace_sources": "Searching across workspace papers…",
        "fetch_external_background": "Fetching background knowledge…",
        "get_paper_metadata": "Loading paper metadata…",
        "get_concept_map": "Loading concept map…",
        "get_guided_questions": "Checking reading progress…",
        "get_session_context": "Loading session context…",
        "discover_sources": "Searching for relevant sources…",
        "manage_sources": "Managing sources…",
        "list_deliverables": "Checking deliverables…",
        "read_deliverable_section": "Reading section content…",
        "draft_deliverable_section": "Initiating draft generation…",
        "get_agenda": "Checking your agenda…",
        "update_agenda": "Updating agenda…",
        "get_workspace_overview": "Loading workspace overview…",
        "navigate_to": "Processing navigation…",
        "search_academic_papers": "Searching academic literature…",
        "get_citation_context": "Loading citation context…",
        "web_search": "Searching the web…",
        "analyze_and_rank_sources": "Analyzing source relevance…",
        "fetch_paper_fulltext": "Fetching paper full text…",
        "check_deliverable_coherence": "Checking document coherence…",
        "suggest_section_transitions": "Suggesting transitions…",
    }.get(tool_name, "")


def _extract_answer_json(state: dict) -> dict | None:
    """Extract structured answer from final graph state."""
    # Check for answer in state (Paper QA path)
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "additional_kwargs"):
            answer_json = msg.additional_kwargs.get("answer_json")
            if answer_json:
                return answer_json
        if hasattr(msg, "type") and msg.type == "ai" and msg.content:
            return {
                "direct_answer": msg.content,
                "key_points": None,
                "evidence": [],
                "plain_language": None,
                "bigger_picture": None,
                "uncertainty": None,
                "answer_mode": state.get("route", "paper_understanding"),
                "scope_label": "",
                "can_expand": False,
            }
    return None


async def _update_session_state(
    session_id: str,
    question: str,
    final_state: dict,
    prev_state: dict,
) -> None:
    """Update Redis session state after a turn."""
    answer_text = ""
    messages = final_state.get("messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "ai" and msg.content:
            answer_text = msg.content[:300]
            break

    turn_count = prev_state.get("turn_count", 0) + 1
    recent_messages = prev_state.get("recent_messages", [])
    recent_messages.append({"q": question, "a": answer_text, "mode": final_state.get("route", "")})
    recent_messages = recent_messages[-5:]

    # Simple summary append (compress every 5 turns handled elsewhere)
    summary = prev_state.get("session_summary", "")
    snippet = answer_text[:200].replace("\n", " ")
    summary = (summary + f"\nQ: {question}\nA: {snippet}").strip()[-2000:]

    await set_session_state(session_id, {
        **prev_state,
        "turn_count": turn_count,
        "recent_messages": recent_messages,
        "session_summary": summary,
    })


async def _load_paper_meta(paper_id: str) -> tuple[str, str]:
    """Load paper title and abstract from DB."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.models.orm import Paper
    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        paper = await db.get(Paper, paper_id)
        if paper:
            return paper.title or "", paper.abstract or ""
    return "", ""


async def _load_workspace_meta(workspace_id: str) -> tuple[str, int]:
    """Load workspace title and paper count."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select, func
    from app.models.orm import Workspace, Paper
    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        workspace = await db.get(Workspace, workspace_id)
        title = workspace.title if workspace else ""
        result = await db.execute(
            select(func.count(Paper.id)).where(Paper.workspace_id == workspace_id)
        )
        count = result.scalar() or 0
    return title, count


async def _get_paper_title(paper_id: str) -> str:
    """Get paper title by ID."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.models.orm import Paper
    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        paper = await db.get(Paper, paper_id)
        return paper.title or paper.filename if paper else ""
