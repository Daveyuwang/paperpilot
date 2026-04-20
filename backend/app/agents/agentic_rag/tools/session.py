"""
Session context tool for the agentic RAG system.
Wraps Redis session state lookup.
"""
from __future__ import annotations

from langchain_core.tools import tool


@tool
async def get_session_context(session_id: str) -> dict:
    """Get the conversation history summary and context for a chat session.

    Use this tool when you need to understand what has already been discussed
    to avoid repeating information or to reference earlier parts of the conversation.

    Args:
        session_id: The session ID.
    """
    from app.db.redis_client import get_session_state

    state = await get_session_state(session_id)
    if not state:
        return {"session_id": session_id, "summary": "New session — no prior context."}

    return {
        "session_id": session_id,
        "summary": state.get("session_summary", ""),
        "turn_count": state.get("turn_count", 0),
        "covered_question_ids": state.get("covered_question_ids", []),
        "explained_terms": state.get("explained_terms", []),
        "session_language": state.get("session_language", ""),
        "recent_messages": state.get("recent_messages", [])[-3:],
    }
