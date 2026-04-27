import uuid
from datetime import datetime
from sqlalchemy import (
    String, Text, Integer, Float, Boolean, DateTime, ForeignKey,
    Enum as SAEnum, JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.db.postgres import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


# ── Workflow Run enums ────────────────────────────────────────────────────


class WorkflowRunStatus(str, enum.Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


class WorkflowRunType(str, enum.Enum):
    deep_research = "deep_research"
    proposal = "proposal"
    plan = "plan"
    deliverable_draft = "deliverable_draft"


# ── Ingestion Stage enum ─────────────────────────────────────────────────


class IngestionStage(str, enum.Enum):
    uploaded = "uploaded"
    text_extracted = "text_extracted"
    chunked = "chunked"
    embedded = "embedded"
    concept_mapped = "concept_mapped"
    scaffolded = "scaffolded"
    ready = "ready"
    failed = "failed"


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    guest_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), default="My Research Workspace")
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    papers: Mapped[list["Paper"]] = relationship(back_populates="workspace")


class PaperStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    error = "error"


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    guest_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    authors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    section_headers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    used_nougat_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[PaperStatus] = mapped_column(
        SAEnum(PaperStatus), default=PaperStatus.pending
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ingestion_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ingestion_progress: Mapped[int] = mapped_column(Integer, default=0)
    ingestion_error_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    chunks: Mapped[list["Chunk"]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    guide_questions: Mapped[list["GuideQuestion"]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    concept_nodes: Mapped[list["ConceptNode"]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    sessions: Mapped[list["Session"]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    workspace: Mapped["Workspace | None"] = relationship(back_populates="papers")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    paper_id: Mapped[str] = mapped_column(String(36), ForeignKey("papers.id", ondelete="CASCADE"))
    qdrant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    section_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(String(64), default="text")  # text|figure|table|caption
    # PDF coordinates for highlight overlay: {page, x0, y0, x1, y1}
    bbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    paper: Mapped["Paper"] = relationship(back_populates="chunks")


class QuestionStage(str, enum.Enum):
    motivation = "motivation"
    approach = "approach"
    experiments = "experiments"
    takeaways = "takeaways"


class GuideQuestion(Base):
    __tablename__ = "guide_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    paper_id: Mapped[str] = mapped_column(String(36), ForeignKey("papers.id", ondelete="CASCADE"))
    question: Mapped[str] = mapped_column(Text)
    stage: Mapped[QuestionStage] = mapped_column(SAEnum(QuestionStage))
    order_index: Mapped[int] = mapped_column(Integer)
    # Which sections this question is anchored to
    anchor_sections: Mapped[list | None] = mapped_column(JSON, nullable=True)

    paper: Mapped["Paper"] = relationship(back_populates="guide_questions")


class ConceptNode(Base):
    __tablename__ = "concept_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    paper_id: Mapped[str] = mapped_column(String(36), ForeignKey("papers.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(512))
    entity_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Which chunk(s) it appears in
    chunk_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    page_numbers: Mapped[list | None] = mapped_column(JSON, nullable=True)

    paper: Mapped["Paper"] = relationship(back_populates="concept_nodes")
    source_edges: Mapped[list["ConceptEdge"]] = relationship(
        foreign_keys="ConceptEdge.source_id", back_populates="source", cascade="all, delete-orphan"
    )
    target_edges: Mapped[list["ConceptEdge"]] = relationship(
        foreign_keys="ConceptEdge.target_id", back_populates="target", cascade="all, delete-orphan"
    )


class ConceptEdge(Base):
    __tablename__ = "concept_edges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    paper_id: Mapped[str] = mapped_column(String(36), ForeignKey("papers.id", ondelete="CASCADE"))
    source_id: Mapped[str] = mapped_column(String(36), ForeignKey("concept_nodes.id", ondelete="CASCADE"))
    target_id: Mapped[str] = mapped_column(String(36), ForeignKey("concept_nodes.id", ondelete="CASCADE"))
    relation: Mapped[str | None] = mapped_column(String(256), nullable=True)

    source: Mapped["ConceptNode"] = relationship(foreign_keys=[source_id], back_populates="source_edges")
    target: Mapped["ConceptNode"] = relationship(foreign_keys=[target_id], back_populates="target_edges")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    guest_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    paper_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("papers.id", ondelete="CASCADE"), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_active: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    paper: Mapped["Paper | None"] = relationship(back_populates="sessions")


class PaperConceptMap(Base):
    """
    Stores the LLM-generated concept map for a paper as a single JSON blob.
    One row per paper (paper_id is the primary key).
    Replaces the old co-occurrence-based ConceptNode/ConceptEdge tables for display.
    """
    __tablename__ = "paper_concept_maps"

    paper_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("papers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Full concept map: {nodes: [...], edges: [...]}
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    guest_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_type: Mapped[WorkflowRunType] = mapped_column(SAEnum(WorkflowRunType))
    status: Mapped[WorkflowRunStatus] = mapped_column(
        SAEnum(WorkflowRunStatus), default=WorkflowRunStatus.running
    )
    input_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stages_completed: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    artifacts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserPreferences(Base):
    """Cross-workspace user preferences / memory."""
    __tablename__ = "user_preferences"

    guest_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    terminology: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    citation_style: Mapped[str | None] = mapped_column(String(64), nullable=True)
    research_domains: Mapped[list | None] = mapped_column(JSON, nullable=True)
    writing_style: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    custom_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
