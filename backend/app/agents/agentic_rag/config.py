"""
Configuration constants for the agentic RAG system.
Budget limits, model names, thresholds.
"""
from __future__ import annotations

from app.config import get_settings

# Budget caps
MAX_TOOL_CALLS_PAPER_QA = 10
MAX_TOOL_CALLS_CONSOLE = 8
MAX_RETRY_COUNT = 3
AGENT_TIMEOUT_SECONDS = 60

# Chunk filter threshold (cross-encoder score)
CHUNK_RELEVANCE_THRESHOLD = 0.3

# Retrieval defaults
RETRIEVAL_TOP_K = 6
RERANK_TOP_K = 5

# Grade thresholds
GRADE_PASS_THRESHOLD = 0.7


def get_model_name() -> str:
    """Return the configured LLM model name for agent nodes."""
    return get_settings().llm_model


def get_anthropic_api_key() -> str:
    s = get_settings()
    return s.llm_api_key or s.anthropic_api_key
