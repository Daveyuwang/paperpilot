"""
Budget enforcement helpers for the agentic RAG system.
Prevents runaway loops by checking tool call counts and retry limits.
"""
from __future__ import annotations

from app.agents.agentic_rag.config import (
    MAX_TOOL_CALLS_PAPER_QA,
    MAX_TOOL_CALLS_CONSOLE,
    MAX_RETRY_COUNT,
)


def paper_qa_budget_exceeded(tool_call_count: int, retry_count: int) -> bool:
    return (
        tool_call_count >= MAX_TOOL_CALLS_PAPER_QA
        or retry_count >= MAX_RETRY_COUNT
    )


def console_budget_exceeded(tool_call_count: int) -> bool:
    return tool_call_count >= MAX_TOOL_CALLS_CONSOLE


def increment_tool_calls(current: int, new_calls: int) -> int:
    return current + new_calls
