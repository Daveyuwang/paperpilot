"""
LangGraph state definitions for the agentic RAG system.
Uses TypedDict with Annotated fields for LangGraph compatibility.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Literal

from langgraph.graph import MessagesState
from langchain_core.messages import BaseMessage


class PaperQAState(MessagesState):
    """State for the Paper QA agentic subgraph."""

    # Context identifiers
    paper_id: str
    session_id: str
    guest_id: str

    # Accumulated retrieval results across all tool calls
    all_retrieved_chunks: Annotated[list[dict], operator.add]

    # After chunk_filter pass
    filtered_chunks: list[dict]

    # Structured answer (same JSON schema as current system)
    answer: dict | None

    # Grading
    grade_result: Literal["pass", "fail", ""]
    rewritten_query: str

    # Budget counters
    retry_count: int
    tool_call_count: int

    # Paper metadata (loaded once)
    paper_title: str
    paper_abstract: str
    guide_questions: list[dict]

    # Session context
    session_summary: str
    covered_question_ids: list[str]
    explained_terms: list[str]
    recent_messages: list[dict]
    session_language: str


class ConsoleState(MessagesState):
    """State for the Console agentic subgraph."""

    # Context identifiers
    workspace_id: str
    session_id: str
    active_paper_id: str

    # Budget counter
    tool_call_count: int


class RouterOutput(MessagesState):
    """State for the top-level router output."""

    route: Literal["paper_qa", "console", "direct_response"]
