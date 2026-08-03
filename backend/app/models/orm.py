import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String, Text, Integer, Float, Boolean, DateTime, ForeignKey,
    Enum as SAEnum, JSON, UniqueConstraint, CheckConstraint, Index,
    event as sa_event,
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
    incomplete = "incomplete"


class WorkflowRunType(str, enum.Enum):
    deep_research = "deep_research"
    proposal = "proposal"
    plan = "plan"
    deliverable_draft = "deliverable_draft"


class DeepResearchArtifactKind(str, enum.Enum):
    """Typed immutable snapshots emitted by the Deep Research workflow."""

    plan = "plan"
    sub_report = "sub_report"
    pre_synthesis_evaluation = "pre_synthesis_evaluation"
    controller_transition = "controller_transition"
    report_candidate = "report_candidate"
    post_synthesis_evaluation = "post_synthesis_evaluation"
    terminal_decision = "terminal_decision"


# ── Research Director lifecycle ───────────────────────────────────


class ResearchArtifactStatus(str, enum.Enum):
    """Lifecycle states for plans and their handoff artifacts.

    Execution and verification are deliberately outside this lifecycle: the
    Research Director can prepare and hand off work, but cannot claim that an
    external implementation or experiment has run successfully.
    """

    draft = "draft"
    reviewed = "reviewed"
    approved = "approved"
    superseded = "superseded"
    handed_off = "handed_off"


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
    research_projects: Mapped[list["ResearchProject"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    research_idempotency_receipts: Mapped[list["ResearchIdempotencyReceipt"]] = (
        relationship(
            back_populates="workspace",
            cascade="all, delete-orphan",
        )
    )
    deep_research_artifact_versions: Mapped[
        list["DeepResearchArtifactVersion"]
    ] = relationship(
        back_populates="workspace",
        passive_deletes=True,
    )
    deep_research_run_events: Mapped[list["DeepResearchRunEvent"]] = relationship(
        back_populates="workspace",
        passive_deletes="all",
    )


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

    deep_research_artifact_versions: Mapped[
        list["DeepResearchArtifactVersion"]
    ] = relationship(
        back_populates="run",
        passive_deletes=True,
        order_by="DeepResearchArtifactVersion.created_at",
    )
    deep_research_run_events: Mapped[list["DeepResearchRunEvent"]] = relationship(
        back_populates="run",
        passive_deletes="all",
        order_by="DeepResearchRunEvent.seq",
    )


class DeepResearchArtifactVersion(Base):
    """Immutable, lineage-aware Deep Research artifact snapshot.

    Application code must create rows through ``deep_research.artifacts`` so
    ownership, canonical hashing, secret rejection, lineage, and idempotency
    checks are applied consistently. Rows intentionally have no ``updated_at``
    field: a correction is represented by a new version.
    """

    __tablename__ = "deep_research_artifact_versions"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "write_key",
            name="uq_deep_research_artifact_versions_run_write_key",
        ),
        UniqueConstraint(
            "run_id",
            "artifact_kind",
            "logical_artifact_id",
            "version_number",
            name="uq_deep_research_artifact_versions_logical_version",
        ),
        CheckConstraint(
            "version_number > 0",
            name="ck_deep_research_artifact_versions_version_positive",
        ),
        CheckConstraint(
            "plan_version >= 0",
            name="ck_deep_research_artifact_versions_plan_version_nonnegative",
        ),
        CheckConstraint(
            "controller_cycle >= 0",
            name="ck_deep_research_artifact_versions_cycle_nonnegative",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_deep_research_artifact_versions_schema_version_positive",
        ),
        CheckConstraint(
            "length(trim(logical_artifact_id)) > 0",
            name="ck_deep_research_artifact_versions_logical_id_nonempty",
        ),
        CheckConstraint(
            "length(trim(write_key)) > 0",
            name="ck_deep_research_artifact_versions_write_key_nonempty",
        ),
        CheckConstraint(
            "length(content_hash) = 64",
            name="ck_deep_research_artifact_versions_hash_length",
        ),
        Index(
            "ix_deep_research_artifact_versions_run_kind_cycle",
            "run_id",
            "artifact_kind",
            "controller_cycle",
        ),
        Index(
            "ix_deep_research_artifact_versions_owner_cycle",
            "run_id",
            "workspace_id",
            "guest_id",
            "controller_cycle",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    guest_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    artifact_kind: Mapped[DeepResearchArtifactKind] = mapped_column(
        SAEnum(
            DeepResearchArtifactKind,
            name="ck_deep_research_artifact_versions_kind",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
            length=32,
        ),
        nullable=False,
    )
    logical_artifact_id: Mapped[str] = mapped_column(String(255), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    controller_cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "deep_research_artifact_versions.id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
        index=True,
    )
    source_checkpoint_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    write_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    run: Mapped["WorkflowRun"] = relationship(
        back_populates="deep_research_artifact_versions"
    )
    workspace: Mapped["Workspace"] = relationship(
        back_populates="deep_research_artifact_versions"
    )
    parent_version: Mapped["DeepResearchArtifactVersion | None"] = relationship(
        remote_side=[id],
        foreign_keys=[parent_version_id],
    )


class DeepResearchRunEvent(Base):
    """Immutable event in one Deep Research run's durable replay stream."""

    __tablename__ = "deep_research_run_events"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "seq",
            name="uq_deep_research_run_events_run_seq",
        ),
        UniqueConstraint(
            "run_id",
            "event_id",
            name="uq_deep_research_run_events_run_event_id",
        ),
        CheckConstraint(
            "seq > 0",
            name="ck_deep_research_run_events_seq_positive",
        ),
        CheckConstraint(
            "schema_version = 'deep-research-event.v1'",
            name="ck_deep_research_run_events_schema_version",
        ),
        CheckConstraint(
            "type IN ('run_started', 'phase_started', 'phase_completed', "
            "'subquestion_upserted', 'subquestion_progressed', "
            "'evaluation_started', 'evaluation_completed', 'route_selected', "
            "'artifact_version_created', 'checkpoint_saved', 'budget_updated', "
            "'synthesis_section_updated', 'run_finished', 'protocol_error')",
            name="ck_deep_research_run_events_type_supported",
        ),
        CheckConstraint(
            "cycle >= 0",
            name="ck_deep_research_run_events_cycle_nonnegative",
        ),
        CheckConstraint(
            "plan_version >= 0",
            name="ck_deep_research_run_events_plan_version_nonnegative",
        ),
        CheckConstraint(
            "corpus_version >= 0",
            name="ck_deep_research_run_events_corpus_version_nonnegative",
        ),
        CheckConstraint(
            "report_version IS NULL OR report_version >= 1",
            name="ck_deep_research_run_events_report_version_positive",
        ),
        Index(
            "ix_deep_research_run_events_run_seq",
            "run_id",
            "seq",
        ),
        Index(
            "ix_deep_research_run_events_owner_seq",
            "run_id",
            "workspace_id",
            "guest_id",
            "seq",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    guest_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    emitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    corpus_version: Mapped[int] = mapped_column(Integer, nullable=False)
    report_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checkpoint_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    run: Mapped["WorkflowRun"] = relationship(
        back_populates="deep_research_run_events"
    )
    workspace: Mapped["Workspace"] = relationship(
        back_populates="deep_research_run_events"
    )


class ImmutableDeepResearchRunEventError(RuntimeError):
    """An existing Deep Research replay event cannot be changed directly."""


@sa_event.listens_for(DeepResearchRunEvent, "before_update", propagate=True)
def _prevent_deep_research_run_event_update(*_args) -> None:
    raise ImmutableDeepResearchRunEventError(
        "Deep Research run events are immutable; append a new event"
    )


@sa_event.listens_for(DeepResearchRunEvent, "before_delete", propagate=True)
def _prevent_deep_research_run_event_delete(*_args) -> None:
    raise ImmutableDeepResearchRunEventError(
        "Deep Research run events can only be deleted with their owning run"
    )


class ResearchProject(Base):
    """Top-level Research Director project owned by a workspace guest."""

    __tablename__ = "research_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    guest_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512))
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ResearchArtifactStatus] = mapped_column(
        SAEnum(ResearchArtifactStatus, name="research_artifact_status"),
        default=ResearchArtifactStatus.draft,
    )
    # Research contract, scope, constraints, and other project-level artifacts.
    content: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="research_projects")
    plan_versions: Mapped[list["ResearchPlanVersion"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ResearchPlanVersion.version_number",
    )
    reviews: Mapped[list["ResearchPlanReview"]] = relationship(
        back_populates="project", order_by="ResearchPlanReview.created_at"
    )
    handoff_bundles: Mapped[list["ResearchHandoffBundle"]] = relationship(
        back_populates="project", order_by="ResearchHandoffBundle.version_number"
    )


class ResearchPlanVersion(Base):
    """Immutable-by-convention snapshot of a research implementation plan."""

    __tablename__ = "research_plan_versions"
    __table_args__ = (
        UniqueConstraint(
            "research_project_id",
            "version_number",
            name="uq_research_plan_versions_project_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    guest_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    research_project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ResearchArtifactStatus] = mapped_column(
        SAEnum(ResearchArtifactStatus, name="research_artifact_status"),
        default=ResearchArtifactStatus.draft,
    )
    # Full plan snapshot, including hypotheses, methods, work packages, and risks.
    content: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    project: Mapped["ResearchProject"] = relationship(back_populates="plan_versions")
    reviews: Mapped[list["ResearchPlanReview"]] = relationship(
        back_populates="plan_version",
        cascade="all, delete-orphan",
        order_by="ResearchPlanReview.review_round",
    )
    handoff_bundles: Mapped[list["ResearchHandoffBundle"]] = relationship(
        back_populates="plan_version",
        cascade="all, delete-orphan",
        order_by="ResearchHandoffBundle.version_number",
    )


class ResearchPlanReview(Base):
    """Independent, structured review of one plan version."""

    __tablename__ = "research_plan_reviews"
    __table_args__ = (
        UniqueConstraint(
            "research_plan_version_id",
            "review_round",
            name="uq_research_plan_reviews_version_round",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    guest_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    research_project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    research_plan_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("research_plan_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    review_round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[ResearchArtifactStatus] = mapped_column(
        SAEnum(ResearchArtifactStatus, name="research_artifact_status"),
        default=ResearchArtifactStatus.draft,
    )
    # Review rubric, findings, issues, decisions, and accepted risks.
    review: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    project: Mapped["ResearchProject"] = relationship(back_populates="reviews")
    plan_version: Mapped["ResearchPlanVersion"] = relationship(back_populates="reviews")


class ResearchHandoffBundle(Base):
    """Approved research package prepared for an external executor."""

    __tablename__ = "research_handoff_bundles"
    __table_args__ = (
        UniqueConstraint(
            "research_project_id",
            "version_number",
            name="uq_research_handoff_bundles_project_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    guest_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    research_project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    research_plan_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("research_plan_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ResearchArtifactStatus] = mapped_column(
        SAEnum(ResearchArtifactStatus, name="research_artifact_status"),
        default=ResearchArtifactStatus.draft,
    )
    # Implementation-ready work packages, acceptance criteria, and open risks.
    content: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    project: Mapped["ResearchProject"] = relationship(back_populates="handoff_bundles")
    plan_version: Mapped["ResearchPlanVersion"] = relationship(
        back_populates="handoff_bundles"
    )


class ResearchIdempotencyReceipt(Base):
    """Durable claim and frozen response for a Research Director mutation."""

    __tablename__ = "research_idempotency_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "guest_id",
            "idempotency_key",
            name="uq_research_idempotency_receipts_scope_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    guest_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="in_progress",
    )
    owner_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    workspace: Mapped["Workspace"] = relationship(
        back_populates="research_idempotency_receipts"
    )


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
