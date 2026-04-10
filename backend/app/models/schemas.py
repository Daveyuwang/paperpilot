from __future__ import annotations
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


# ── Paper ─────────────────────────────────────────────────────────────────

class PaperOut(BaseModel):
    id: str
    filename: str
    title: str | None
    abstract: str | None
    authors: list[str] | None
    section_headers: list[str] | None
    page_count: int | None
    parse_confidence: float | None
    used_nougat_fallback: bool
    status: str
    error_message: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class PaperListItem(BaseModel):
    id: str
    filename: str
    title: str | None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# ── Chunks ────────────────────────────────────────────────────────────────

class ChunkOut(BaseModel):
    id: str
    qdrant_id: str | None
    content: str
    section_title: str | None
    page_number: int | None
    chunk_index: int
    content_type: str
    bbox: dict[str, Any] | None

    class Config:
        from_attributes = True


# ── Guide Questions ───────────────────────────────────────────────────────

class GuideQuestionOut(BaseModel):
    id: str
    question: str
    stage: str
    order_index: int
    anchor_sections: list[str] | None

    class Config:
        from_attributes = True


# ── Concept Map (LLM-generated, grounded) ─────────────────────────────────

class ConceptNodeOut(BaseModel):
    id: str
    label: str
    type: str          # Problem | Method | Component | Baseline | Dataset | Metric | Finding | Limitation
    short_description: str
    evidence: list[str]
    section: str | None = None
    page: int | None = None


class ConceptEdgeOut(BaseModel):
    source: str
    target: str
    relation: str      # addresses | consists_of | compared_with | evaluated_on | measured_by | leads_to | limited_by
    evidence: list[str]


class ConceptMapOut(BaseModel):
    nodes: list[ConceptNodeOut]
    edges: list[ConceptEdgeOut]
    generated: bool = False  # False = no map exists yet for this paper


# ── Session ───────────────────────────────────────────────────────────────

class SessionOut(BaseModel):
    id: str
    guest_id: str | None = None
    paper_id: str
    created_at: datetime
    last_active: datetime

    class Config:
        from_attributes = True


# ── QA ───────────────────────────────────────────────────────────────────

class QARequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    question_id: str | None = None  # set if following a guide question

    class Config:
        from_attributes = True


# ── WebSocket message envelopes ───────────────────────────────────────────

class WSMessageType(str):
    TOKEN = "token"
    CHUNK_REFS = "chunk_refs"
    ANSWER_DONE = "answer_done"
    NEXT_QUESTION = "next_question"
    ERROR = "error"
    STATUS = "status"
