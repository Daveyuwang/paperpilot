"""
LangGraph agent state type definition.
All fields are serializable to/from Redis JSON.
"""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field, PrivateAttr


class AgentState(BaseModel):
    # Identifiers
    session_id: str
    paper_id: str
    guest_id: str = ""

    # Current turn inputs
    question: str = ""
    question_id: str | None = None  # set if following a guided question

    # Routing
    input_type: Literal["guided", "free"] = "free"
    intent: str = "paper_understanding"
    answer_mode: str = "paper_understanding"
    mode_override: str | None = None  # set by client to bypass intent classification

    # Query enrichment (internal only — never shown to user)
    enriched_query: str = ""

    # Retrieval results for current turn
    retrieved_chunks: list[dict] = Field(default_factory=list)
    anchor_sections: list[str] = Field(default_factory=list)

    # Evidence extraction (runs before synthesis)
    extracted_evidence: list[dict] = Field(default_factory=list)
    evidence_confidence: float = 0.5
    coverage_gap: str = ""

    # Synthesis output
    answer_text: str = ""
    answer_json: dict | None = None
    citations: list[dict] = Field(default_factory=list)  # {chunk_id, section_title, page}
    needs_external: bool = False
    external_context: str = ""
    unknown_terms: list[str] = Field(default_factory=list)

    # Session memory (persisted in Redis)
    covered_question_ids: list[str] = Field(default_factory=list)
    covered_stages: list[str] = Field(default_factory=list)
    explained_terms: list[str] = Field(default_factory=list)
    session_summary: str = ""
    turn_count: int = 0

    # Router confidence (set by route_input, used in request_trace)
    router_confidence: float = 0.0

    # Next suggested questions (1 primary + up to 2 secondary)
    next_question: dict | None = None  # kept for backward compat
    suggested_questions: list[dict] = Field(default_factory=list)  # [{id, question, stage, is_primary}]

    # Paper metadata (loaded once per session)
    paper_title: str = ""
    paper_abstract: str = ""
    guide_questions: list[dict] = Field(default_factory=list)

    # Session context — persisted in Redis alongside covered_question_ids etc.
    recent_messages: list[dict] = Field(default_factory=list)  # last 5: [{q, a, mode}]
    session_language: str = ""  # "zh" | "en" | "" (empty = not yet locked)

    # Per-turn trace metadata — populated by nodes, consumed by graph's request_trace log
    trace_metadata: dict = Field(default_factory=dict)

    # Private runtime-only attrs (not serialized)
    _stream_callback: Any = PrivateAttr(default=None)
    _llm_client: Any = PrivateAttr(default=None)

    class Config:
        arbitrary_types_allowed = True
