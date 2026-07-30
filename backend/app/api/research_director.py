from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.drafts import _resolve_llm
from app.api.guest import require_guest_id
from app.db.postgres import AsyncSessionLocal, get_db
from app.models.orm import (
    ResearchArtifactStatus,
    ResearchHandoffBundle,
    ResearchIdempotencyReceipt,
    ResearchPlanReview,
    ResearchPlanVersion,
    ResearchProject,
    Workspace,
)
from app.rate_limit import limiter
from app.research_director.models import (
    DEFAULT_REVIEWER_PERSPECTIVES,
    EvidenceItem,
    ExperimentPlan,
    GeneratePlanRequest,
    ImplementationPlan,
    MethodSpec,
    PlanLifecycleStatus,
    ResearchContract,
    ResearchHypothesis,
    ResearchPlanBundle,
    ReviewerPerspective,
    ReviewIssueStatus,
    ReviewPlanRequest,
    ReviewReport,
    ReviewSeverity,
    ReviewVerdict,
    RevisePlanRequest,
    review_report_digest,
    scientific_plan_digest,
)
from app.research_director.service import generate_plan, review_plan, revise_plan

router = APIRouter()


IDEMPOTENCY_LEASE = timedelta(minutes=2)
IDEMPOTENCY_HEARTBEAT_SECONDS = 30
IDEMPOTENCY_IN_PROGRESS = "in_progress"
IDEMPOTENCY_COMPLETED = "completed"

IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        description="Durable replay key for this workspace mutation.",
    ),
]


@limiter.limit("3/hour")
def _fresh_create_rate_scope(request: Request) -> None:
    pass


@limiter.limit("3/hour")
def _fresh_review_rate_scope(request: Request) -> None:
    pass


@limiter.limit("3/hour")
def _fresh_revise_rate_scope(request: Request) -> None:
    pass


_FRESH_RATE_SCOPES = {
    "research_project.create": _fresh_create_rate_scope,
    "research_plan.review": _fresh_review_rate_scope,
    "research_plan.revise": _fresh_revise_rate_scope,
}


@dataclass(frozen=True)
class _IdempotencyClaim:
    receipt_id: str
    owner_token: str
    workspace_id: str
    guest_id: str
    operation: str
    request_fingerprint: str


@dataclass(frozen=True)
class _IdempotencyResolution:
    claim: _IdempotencyClaim | None = None
    replay: ResearchProjectDetail | JSONResponse | None = None


BriefListItem = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]
ApiNonEmptyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
ProjectTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]


class ResearchSourcePolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    use_workspace_sources: bool
    discover_external_sources: bool
    prefer_primary_sources: bool
    time_horizon: str = Field(min_length=1, max_length=256)
    must_include: list[BriefListItem] = Field(default_factory=list, max_length=100)
    must_exclude: list[BriefListItem] = Field(default_factory=list, max_length=100)


class ResearchBriefSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=512)
    research_question: str = Field(min_length=1, max_length=10_000)
    objective: str = Field(min_length=1, max_length=10_000)
    problem_statement: str = Field(default="", max_length=20_000)
    intended_contribution: str = Field(default="", max_length=20_000)
    scope: str = Field(default="", max_length=20_000)
    success_criteria: list[BriefListItem] = Field(min_length=1, max_length=100)
    constraints: list[BriefListItem] = Field(default_factory=list, max_length=100)
    desired_deliverables: list[BriefListItem] = Field(min_length=1, max_length=100)
    source_policy: ResearchSourcePolicySnapshot
    notes: str = Field(default="", max_length=20_000)


class CreateResearchProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    title: ProjectTitle | None = None
    brief_snapshot: ResearchBriefSnapshot | None = None
    plan_request: GeneratePlanRequest


class ReviewResearchPlanRequest(BaseModel):
    evidence: list[EvidenceItem] = Field(default_factory=list)
    perspectives: list[ReviewerPerspective] = Field(
        default_factory=lambda: list(DEFAULT_REVIEWER_PERSPECTIVES),
        min_length=1,
    )
    review_instructions: str | None = None

    @field_validator("evidence")
    @classmethod
    def require_unique_evidence_ids(
        cls, value: list[EvidenceItem]
    ) -> list[EvidenceItem]:
        return _validate_unique_evidence_ids(value)

    @field_validator("perspectives")
    @classmethod
    def require_complete_review_coverage(
        cls, value: list[ReviewerPerspective]
    ) -> list[ReviewerPerspective]:
        required = set(DEFAULT_REVIEWER_PERSPECTIVES)
        provided = set(value)
        if provided != required or len(value) != len(required):
            missing = sorted(item.value for item in required - provided)
            raise ValueError(
                "independent review requires every default perspective; "
                f"missing: {missing}"
            )
        return value


class ReviseResearchPlanRequest(BaseModel):
    review_id: str | None = Field(
        default=None,
        description=(
            "Optional optimistic token for the latest independent review. A stale "
            "or historical review ID is rejected."
        ),
    )
    evidence: list[EvidenceItem] = Field(default_factory=list)
    evidence_warnings: list[BriefListItem] = Field(default_factory=list)
    revision_instructions: str | None = None

    @field_validator("evidence")
    @classmethod
    def require_unique_evidence_ids(
        cls, value: list[EvidenceItem]
    ) -> list[EvidenceItem]:
        return _validate_unique_evidence_ids(value)


class ConfirmResearchHandoffRequest(BaseModel):
    confirm_transfer: Literal[True]


class ResearchProjectSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ApiNonEmptyStr
    workspace_id: ApiNonEmptyStr
    title: ProjectTitle
    objective: str | None
    brief_snapshot: ResearchBriefSnapshot | None = None
    status: ResearchArtifactStatus
    latest_version_number: int | None = Field(default=None, ge=1)
    created_at: datetime
    updated_at: datetime


class ResearchPlanVersionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ApiNonEmptyStr
    version_number: int = Field(ge=1)
    status: ResearchArtifactStatus
    content: ResearchPlanBundle
    created_at: datetime
    updated_at: datetime

    @field_validator("content", mode="before")
    @classmethod
    def require_canonical_frozen_plan(cls, value: Any) -> ResearchPlanBundle:
        if isinstance(value, ResearchPlanBundle):
            return value
        plan = ResearchPlanBundle.model_validate(value)
        if plan.model_dump(mode="json") != value:
            raise ValueError("persisted plan snapshot is not canonical")
        return plan

    @model_validator(mode="after")
    def validate_snapshot_identity_and_lifecycle(self) -> ResearchPlanVersionOut:
        if self.content.version != self.version_number:
            raise ValueError(
                "persisted plan version number does not match its content snapshot"
            )
        allowed_lifecycles = {
            # A generated version is draft; a revised version deliberately
            # remains review_required while its outer artifact is editable.
            ResearchArtifactStatus.draft: {
                PlanLifecycleStatus.DRAFT,
                PlanLifecycleStatus.REVIEW_REQUIRED,
            },
            ResearchArtifactStatus.reviewed: {PlanLifecycleStatus.REVIEW_REQUIRED},
            ResearchArtifactStatus.approved: {
                PlanLifecycleStatus.APPROVED_FOR_HANDOFF
            },
            ResearchArtifactStatus.superseded: {PlanLifecycleStatus.REVIEW_REQUIRED},
            ResearchArtifactStatus.handed_off: {PlanLifecycleStatus.HANDED_OFF},
        }[self.status]
        if self.content.lifecycle_status not in allowed_lifecycles:
            raise ValueError(
                "persisted plan lifecycle does not match its artifact status"
            )
        return self


class ResearchPlanReviewOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ApiNonEmptyStr
    plan_version_id: ApiNonEmptyStr
    review_round: int = Field(ge=1)
    status: ResearchArtifactStatus
    review: ReviewReport
    created_at: datetime

    @field_validator("review", mode="before")
    @classmethod
    def require_canonical_frozen_review(cls, value: Any) -> ReviewReport:
        if isinstance(value, ReviewReport):
            return value
        review = ReviewReport.model_validate(value)
        if review.model_dump(mode="json") != value:
            raise ValueError("persisted review snapshot is not canonical")
        return review

    @model_validator(mode="after")
    def require_completed_review_status(self) -> ResearchPlanReviewOut:
        if self.status != ResearchArtifactStatus.reviewed:
            raise ValueError("persisted independent review must have reviewed status")
        return self


class ResearchHandoffContent(BaseModel):
    """Frozen, typed external-handoff package persisted in the JSON column."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = "1.0"
    bundle_id: ApiNonEmptyStr
    project_id: ApiNonEmptyStr
    plan_version_id: ApiNonEmptyStr
    plan_version_number: int = Field(ge=1)
    status: Literal["ready_for_handoff", "handed_off"]
    execution_status: Literal["awaiting_external_execution"]
    plan_snapshot: ResearchPlanBundle
    research_contract: ResearchContract
    hypotheses: list[ResearchHypothesis]
    methods: list[MethodSpec]
    experiment_plans: list[ExperimentPlan]
    implementation_plan: ImplementationPlan
    independent_review: ReviewReport
    boundary: ApiNonEmptyStr

    @model_validator(mode="after")
    def validate_frozen_duplicates_and_state(self) -> ResearchHandoffContent:
        plan = self.plan_snapshot
        if self.plan_version_number != plan.version:
            raise ValueError(
                "handoff plan_version_number does not match plan_snapshot.version"
            )
        if self.research_contract != plan.contract:
            raise ValueError("handoff research_contract differs from plan_snapshot")
        if self.hypotheses != plan.hypotheses:
            raise ValueError("handoff hypotheses differ from plan_snapshot")
        if self.methods != plan.methods:
            raise ValueError("handoff methods differ from plan_snapshot")
        if self.experiment_plans != plan.experiments:
            raise ValueError("handoff experiment_plans differ from plan_snapshot")
        if self.implementation_plan != plan.implementation_plan:
            raise ValueError("handoff implementation_plan differs from plan_snapshot")

        review = self.independent_review
        if review.reviewed_plan_id != plan.plan_id:
            raise ValueError("handoff review targets a different plan ID")
        if review.reviewed_plan_version != plan.version:
            raise ValueError("handoff review targets a different plan version")
        if review.reviewed_plan_digest != scientific_plan_digest(plan):
            raise ValueError("handoff review targets a different plan snapshot")

        expected_lifecycle = {
            "ready_for_handoff": PlanLifecycleStatus.APPROVED_FOR_HANDOFF,
            "handed_off": PlanLifecycleStatus.HANDED_OFF,
        }[self.status]
        if plan.lifecycle_status != expected_lifecycle:
            raise ValueError("handoff status does not match plan lifecycle")
        return self


class ResearchHandoffOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ApiNonEmptyStr
    plan_version_id: ApiNonEmptyStr
    version_number: int = Field(ge=1)
    status: ResearchArtifactStatus
    content: ResearchHandoffContent
    created_at: datetime

    @field_validator("content", mode="before")
    @classmethod
    def require_canonical_frozen_handoff(
        cls, value: Any
    ) -> ResearchHandoffContent:
        if isinstance(value, ResearchHandoffContent):
            return value
        content = ResearchHandoffContent.model_validate(value)
        if content.model_dump(mode="json") != value:
            raise ValueError("persisted handoff snapshot is not canonical")
        return content

    @model_validator(mode="after")
    def validate_outer_identity_and_status(self) -> ResearchHandoffOut:
        if self.id != self.content.bundle_id:
            raise ValueError("handoff bundle ID differs from frozen content")
        if self.plan_version_id != self.content.plan_version_id:
            raise ValueError("handoff plan version ID differs from frozen content")
        if self.version_number != self.content.plan_version_number:
            raise ValueError("handoff version number differs from frozen content")
        expected_status = {
            ResearchArtifactStatus.approved: "ready_for_handoff",
            ResearchArtifactStatus.handed_off: "handed_off",
        }.get(self.status)
        if expected_status is None or self.content.status != expected_status:
            raise ValueError("handoff artifact status differs from frozen content")
        return self


class ResearchProjectDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ResearchProjectSummary
    plan_versions: list[ResearchPlanVersionOut] = Field(min_length=1)
    reviews: list[ResearchPlanReviewOut]
    handoff_bundles: list[ResearchHandoffOut]

    @model_validator(mode="after")
    def validate_persisted_artifact_graph(self) -> ResearchProjectDetail:
        version_ids = [item.id for item in self.plan_versions]
        version_numbers = [item.version_number for item in self.plan_versions]
        if len(version_ids) != len(set(version_ids)):
            raise ValueError("persisted plan version IDs must be unique")
        if len(version_numbers) != len(set(version_numbers)):
            raise ValueError("persisted plan version numbers must be unique")

        versions_by_id = {item.id: item for item in self.plan_versions}
        latest = max(self.plan_versions, key=lambda item: item.version_number)
        if self.project.latest_version_number != latest.version_number:
            raise ValueError("project latest version number is inconsistent")
        if self.project.status != latest.status:
            raise ValueError("project status does not match its latest plan version")

        review_ids = [item.id for item in self.reviews]
        if len(review_ids) != len(set(review_ids)):
            raise ValueError("persisted review IDs must be unique")
        review_rows = [
            (item.plan_version_id, item.review_round) for item in self.reviews
        ]
        if len(review_rows) != len(set(review_rows)):
            raise ValueError("persisted review rounds must be unique per plan version")
        for item in self.reviews:
            version = versions_by_id.get(item.plan_version_id)
            if version is None:
                raise ValueError("persisted review references an unknown plan version")
            if item.review.reviewed_plan_id != version.content.plan_id:
                raise ValueError("persisted review targets a different plan ID")
            if item.review.reviewed_plan_version != version.version_number:
                raise ValueError("persisted review targets a different plan version")
            if item.review.reviewed_plan_digest != scientific_plan_digest(
                version.content
            ):
                raise ValueError("persisted review targets a different plan snapshot")

        handoff_ids = [item.id for item in self.handoff_bundles]
        handoff_versions = [item.plan_version_id for item in self.handoff_bundles]
        if len(handoff_ids) != len(set(handoff_ids)):
            raise ValueError("persisted handoff IDs must be unique")
        if len(handoff_versions) != len(set(handoff_versions)):
            raise ValueError("persisted handoffs must be unique per plan version")

        review_snapshots = [item.review for item in self.reviews]
        for item in self.handoff_bundles:
            version = versions_by_id.get(item.plan_version_id)
            if version is None:
                raise ValueError("persisted handoff references an unknown plan version")
            if item.content.project_id != self.project.id:
                raise ValueError("persisted handoff references a different project")
            if item.content.plan_snapshot != version.content:
                raise ValueError("persisted handoff plan snapshot differs from its version")
            if item.content.independent_review not in review_snapshots:
                raise ValueError("persisted handoff review has no durable review row")
        return self


def _stored_snapshot_error(label: str) -> HTTPException:
    return HTTPException(
        status_code=500,
        detail=f"Stored {label} failed integrity validation.",
    )


def _load_persisted_plan(value: Any) -> ResearchPlanBundle:
    try:
        plan = ResearchPlanBundle.model_validate(value)
    except ValidationError as exc:
        raise _stored_snapshot_error("research plan snapshot") from exc
    if not isinstance(value, dict) or plan.model_dump(mode="json") != value:
        raise _stored_snapshot_error("research plan snapshot")
    return plan


def _load_persisted_review(value: Any) -> ReviewReport:
    try:
        review = ReviewReport.model_validate(value)
    except ValidationError as exc:
        raise _stored_snapshot_error("independent review snapshot") from exc
    if not isinstance(value, dict) or review.model_dump(mode="json") != value:
        raise _stored_snapshot_error("independent review snapshot")
    return review


def _load_persisted_handoff(value: Any) -> ResearchHandoffContent:
    try:
        content = ResearchHandoffContent.model_validate(value)
    except ValidationError as exc:
        raise _stored_snapshot_error("research handoff snapshot") from exc
    if not isinstance(value, dict) or content.model_dump(mode="json") != value:
        raise _stored_snapshot_error("research handoff snapshot")
    return content


@router.post(
    "/projects",
    response_model=ResearchProjectDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_research_project(
    body: CreateResearchProjectRequest,
    request: Request,
    idempotency_key: IdempotencyKeyHeader,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_workspace(db, body.workspace_id, guest_id)
    operation = "research_project.create"
    async with _idempotency_guard(
        db,
        workspace_id=body.workspace_id,
        guest_id=guest_id,
        idempotency_key=idempotency_key,
        operation=operation,
        request_payload=body.model_dump(mode="json"),
    ) as idempotency:
        if idempotency.replay is not None:
            return idempotency.replay
        assert idempotency.claim is not None
        _enforce_fresh_llm_rate_limit(request, operation)
        llm = await _resolve_llm(guest_id)
        plan = await generate_plan(llm, body.plan_request)
        await _get_owned_workspace(db, body.workspace_id, guest_id)

        project = ResearchProject(
            workspace_id=body.workspace_id,
            guest_id=guest_id,
            title=(body.title or plan.contract.title).strip(),
            objective=plan.contract.objective,
            status=ResearchArtifactStatus.draft,
            content={
                "research_brief": body.plan_request.research_brief,
                "brief_snapshot": (
                    body.brief_snapshot.model_dump(mode="json")
                    if body.brief_snapshot is not None
                    else None
                ),
                "contract": plan.contract.model_dump(mode="json"),
                "boundary": "Planning and review only; execution remains external.",
            },
        )
        db.add(project)
        await db.flush()

        version = ResearchPlanVersion(
            workspace_id=body.workspace_id,
            guest_id=guest_id,
            research_project_id=project.id,
            version_number=1,
            status=ResearchArtifactStatus.draft,
            content=plan.model_dump(mode="json"),
        )
        db.add(version)
        return await _freeze_detail_and_commit(
            db,
            project.id,
            guest_id,
            idempotency_claim=idempotency.claim,
            response_status_code=status.HTTP_201_CREATED,
        )


@router.get("/projects", response_model=list[ResearchProjectSummary])
async def list_research_projects(
    workspace_id: str = Query(...),
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_workspace(db, workspace_id, guest_id)
    latest_version = (
        select(
            ResearchPlanVersion.research_project_id,
            func.max(ResearchPlanVersion.version_number).label("latest_version_number"),
        )
        .where(
            ResearchPlanVersion.workspace_id == workspace_id,
            ResearchPlanVersion.guest_id == guest_id,
        )
        .group_by(ResearchPlanVersion.research_project_id)
        .subquery()
    )
    result = await db.execute(
        select(ResearchProject, latest_version.c.latest_version_number)
        .outerjoin(
            latest_version,
            latest_version.c.research_project_id == ResearchProject.id,
        )
        .where(
            ResearchProject.workspace_id == workspace_id,
            ResearchProject.guest_id == guest_id,
        )
        .order_by(ResearchProject.updated_at.desc())
    )
    return [_summary(project, latest) for project, latest in result.all()]


@router.get("/projects/{project_id}", response_model=ResearchProjectDetail)
async def get_research_project(
    project_id: str,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    return await _project_detail(db, project_id, guest_id)


@router.post(
    "/projects/{project_id}/versions/{version_number}/review",
    response_model=ResearchProjectDetail,
)
async def review_research_plan(
    project_id: str,
    version_number: int,
    body: ReviewResearchPlanRequest,
    request: Request,
    idempotency_key: IdempotencyKeyHeader,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    project, version = await _get_owned_version(
        db, project_id, version_number, guest_id
    )
    operation = "research_plan.review"
    request_payload = {
        "project_id": project_id,
        "version_number": version_number,
        "body": body.model_dump(mode="json"),
    }
    async with _idempotency_guard(
        db,
        workspace_id=project.workspace_id,
        guest_id=guest_id,
        idempotency_key=idempotency_key,
        operation=operation,
        request_payload=request_payload,
    ) as idempotency:
        if idempotency.replay is not None:
            return idempotency.replay
        assert idempotency.claim is not None
        _enforce_fresh_llm_rate_limit(request, operation)
        _ensure_reviewable(version.status)

        plan = _load_persisted_plan(version.content)
        _ensure_review_evidence_matches_plan(plan.evidence_catalog, body.evidence)
        llm = await _resolve_llm(guest_id)
        report = await review_plan(
            llm,
            ReviewPlanRequest(
                plan=plan,
                evidence=body.evidence,
                perspectives=body.perspectives,
                review_instructions=body.review_instructions,
            ),
        )
        report = report.model_copy(
            update={"reviewed_plan_digest": scientific_plan_digest(plan)}
        )

        project, version = await _get_owned_version(
            db,
            project_id,
            version_number,
            guest_id,
            for_update=True,
        )
        _ensure_reviewable(version.status)
        locked_plan = _load_persisted_plan(version.content)
        _ensure_review_targets_plan_snapshot(report, locked_plan)

        round_result = await db.execute(
            select(func.max(ResearchPlanReview.review_round)).where(
                ResearchPlanReview.research_plan_version_id == version.id
            )
        )
        review_round = (round_result.scalar_one_or_none() or 0) + 1
        review = ResearchPlanReview(
            workspace_id=project.workspace_id,
            guest_id=guest_id,
            research_project_id=project.id,
            research_plan_version_id=version.id,
            review_round=review_round,
            status=ResearchArtifactStatus.reviewed,
            review=report.model_dump(mode="json"),
        )
        db.add(review)
        version.status = ResearchArtifactStatus.reviewed
        version.content = {
            **plan.model_dump(mode="json"),
            "lifecycle_status": PlanLifecycleStatus.REVIEW_REQUIRED.value,
        }
        project.status = ResearchArtifactStatus.reviewed
        return await _freeze_detail_and_commit(
            db,
            project.id,
            guest_id,
            conflict_detail=(
                "A concurrent review already created this review round. "
                "Refresh and retry."
            ),
            idempotency_claim=idempotency.claim,
        )


@router.post(
    "/projects/{project_id}/versions/{version_number}/revise",
    response_model=ResearchProjectDetail,
)
async def revise_research_plan(
    project_id: str,
    version_number: int,
    body: ReviseResearchPlanRequest,
    request: Request,
    idempotency_key: IdempotencyKeyHeader,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    project, version = await _get_owned_version(
        db, project_id, version_number, guest_id
    )
    operation = "research_plan.revise"
    request_payload = {
        "project_id": project_id,
        "version_number": version_number,
        "body": body.model_dump(mode="json"),
    }
    async with _idempotency_guard(
        db,
        workspace_id=project.workspace_id,
        guest_id=guest_id,
        idempotency_key=idempotency_key,
        operation=operation,
        request_payload=request_payload,
    ) as idempotency:
        if idempotency.replay is not None:
            return idempotency.replay
        assert idempotency.claim is not None
        _enforce_fresh_llm_rate_limit(request, operation)
        _ensure_revisable(version.status)

        review = await _select_review(db, version.id, None, guest_id)
        _ensure_expected_latest_review(body.review_id, review.id)
        plan = _load_persisted_plan(version.content)
        _ensure_append_only_evidence(plan.evidence_catalog, body.evidence)
        report = _load_persisted_review(review.review)
        _ensure_review_targets_plan_snapshot(report, plan)
        selected_review_id = review.id
        selected_review_round = review.review_round
        selected_review_digest = review_report_digest(report)
        await db.rollback()
        llm = await _resolve_llm(guest_id)
        revised = await revise_plan(
            llm,
            RevisePlanRequest(
                plan=plan,
                review=report,
                evidence=body.evidence,
                evidence_warnings=body.evidence_warnings,
                revision_instructions=body.revision_instructions,
            ),
        )

        project, version = await _get_owned_version(
            db,
            project_id,
            version_number,
            guest_id,
            for_update=True,
        )
        _ensure_revisable(version.status)
        locked_plan = _load_persisted_plan(version.content)
        _ensure_review_targets_plan_snapshot(report, locked_plan)
        locked_review = await _select_review(
            db,
            version.id,
            None,
            guest_id,
        )
        locked_report = _load_persisted_review(locked_review.review)
        _ensure_review_targets_plan_snapshot(locked_report, locked_plan)
        _ensure_revision_review_stable(
            selected_review_id=selected_review_id,
            selected_review_round=selected_review_round,
            selected_review_digest=selected_review_digest,
            locked_review_id=locked_review.id,
            locked_review_round=locked_review.review_round,
            locked_review_digest=review_report_digest(locked_report),
        )

        version.status = ResearchArtifactStatus.superseded
        next_version = ResearchPlanVersion(
            workspace_id=project.workspace_id,
            guest_id=guest_id,
            research_project_id=project.id,
            version_number=revised.version,
            status=ResearchArtifactStatus.draft,
            content=revised.model_dump(mode="json"),
        )
        db.add(next_version)
        project.status = ResearchArtifactStatus.draft
        project.content = {
            **(project.content or {}),
            "contract": revised.contract.model_dump(mode="json"),
        }
        return await _freeze_detail_and_commit(
            db,
            project.id,
            guest_id,
            conflict_detail=(
                "A concurrent revision already created this plan version. "
                "Refresh and retry."
            ),
            idempotency_claim=idempotency.claim,
        )


@router.post(
    "/projects/{project_id}/versions/{version_number}/approve",
    response_model=ResearchProjectDetail,
)
async def approve_research_plan(
    project_id: str,
    version_number: int,
    idempotency_key: IdempotencyKeyHeader,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    project, version = await _get_owned_version(
        db, project_id, version_number, guest_id
    )
    operation = "research_plan.approve"
    async with _idempotency_guard(
        db,
        workspace_id=project.workspace_id,
        guest_id=guest_id,
        idempotency_key=idempotency_key,
        operation=operation,
        request_payload={
            "project_id": project_id,
            "version_number": version_number,
        },
    ) as idempotency:
        if idempotency.replay is not None:
            return idempotency.replay
        assert idempotency.claim is not None
        project, version = await _get_owned_version(
            db, project_id, version_number, guest_id, for_update=True
        )
        if version.status != ResearchArtifactStatus.reviewed:
            raise HTTPException(
                status_code=409,
                detail="Only a reviewed plan can be approved.",
            )
        review = await _select_review(db, version.id, None, guest_id)
        report = _load_persisted_review(review.review)
        plan = _load_persisted_plan(version.content)
        _ensure_review_targets_plan_snapshot(report, plan)
        _ensure_approvable(report, plan)
        approved = plan.model_copy(
            update={"lifecycle_status": PlanLifecycleStatus.APPROVED_FOR_HANDOFF}
        )
        version.status = ResearchArtifactStatus.approved
        version.content = approved.model_dump(mode="json")
        project.status = ResearchArtifactStatus.approved
        return await _freeze_detail_and_commit(
            db,
            project.id,
            guest_id,
            idempotency_claim=idempotency.claim,
        )


@router.post(
    "/projects/{project_id}/versions/{version_number}/prepare-handoff",
    response_model=ResearchProjectDetail,
)
async def prepare_research_handoff(
    project_id: str,
    version_number: int,
    idempotency_key: IdempotencyKeyHeader,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    project, version = await _get_owned_version(
        db, project_id, version_number, guest_id
    )
    operation = "research_handoff.prepare"
    async with _idempotency_guard(
        db,
        workspace_id=project.workspace_id,
        guest_id=guest_id,
        idempotency_key=idempotency_key,
        operation=operation,
        request_payload={
            "project_id": project_id,
            "version_number": version_number,
        },
    ) as idempotency:
        if idempotency.replay is not None:
            return idempotency.replay
        assert idempotency.claim is not None
        project, version = await _get_owned_version(
            db, project_id, version_number, guest_id, for_update=True
        )
        if version.status != ResearchArtifactStatus.approved:
            raise HTTPException(
                status_code=409,
                detail="Approve the plan before preparing handoff.",
            )
        existing = await db.execute(
            select(ResearchHandoffBundle).where(
                ResearchHandoffBundle.research_project_id == project.id,
                ResearchHandoffBundle.version_number == version.version_number,
                ResearchHandoffBundle.guest_id == guest_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail="A handoff bundle already exists for this plan version.",
            )

        plan = _load_persisted_plan(version.content)
        review = await _select_review(db, version.id, None, guest_id)
        report = _load_persisted_review(review.review)
        _ensure_review_targets_plan_snapshot(report, plan)
        _ensure_approvable(report, plan)
        bundle_id = str(uuid.uuid4())
        content = _build_prepared_handoff_content(
            bundle_id=bundle_id,
            project_id=project.id,
            plan_version_id=version.id,
            plan=plan,
            review=report,
        )
        bundle = ResearchHandoffBundle(
            id=bundle_id,
            workspace_id=project.workspace_id,
            guest_id=guest_id,
            research_project_id=project.id,
            research_plan_version_id=version.id,
            version_number=version.version_number,
            status=ResearchArtifactStatus.approved,
            content=content.model_dump(mode="json"),
        )
        db.add(bundle)
        return await _freeze_detail_and_commit(
            db,
            project.id,
            guest_id,
            conflict_detail="A handoff bundle already exists for this plan version.",
            idempotency_claim=idempotency.claim,
        )


@router.post(
    "/projects/{project_id}/versions/{version_number}/handoff",
    response_model=ResearchProjectDetail,
)
async def handoff_research_plan(
    project_id: str,
    version_number: int,
    body: ConfirmResearchHandoffRequest,
    idempotency_key: IdempotencyKeyHeader,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    project, version = await _get_owned_version(
        db, project_id, version_number, guest_id
    )
    operation = "research_handoff.confirm"
    async with _idempotency_guard(
        db,
        workspace_id=project.workspace_id,
        guest_id=guest_id,
        idempotency_key=idempotency_key,
        operation=operation,
        request_payload={
            "project_id": project_id,
            "version_number": version_number,
            "body": body.model_dump(mode="json"),
        },
    ) as idempotency:
        if idempotency.replay is not None:
            return idempotency.replay
        assert idempotency.claim is not None
        project, version = await _get_owned_version(
            db, project_id, version_number, guest_id, for_update=True
        )
        if version.status == ResearchArtifactStatus.handed_off:
            raise HTTPException(
                status_code=409,
                detail="This plan version was already handed off.",
            )
        if version.status != ResearchArtifactStatus.approved:
            raise HTTPException(
                status_code=409,
                detail="Approve the plan before handoff.",
            )

        bundle = await _select_handoff_bundle(
            db,
            project_id=project.id,
            version_number=version.version_number,
            guest_id=guest_id,
            for_update=True,
        )
        if bundle is None:
            raise HTTPException(
                status_code=409,
                detail="Prepare a handoff bundle before confirming transfer.",
            )
        if bundle.status == ResearchArtifactStatus.handed_off:
            raise HTTPException(
                status_code=409,
                detail="This plan version was already handed off.",
            )
        if bundle.status != ResearchArtifactStatus.approved:
            raise HTTPException(
                status_code=409,
                detail="The handoff bundle is not ready for transfer.",
            )

        plan = _load_persisted_plan(version.content)
        handed_off = _mark_plan_handed_off(plan)
        bundle.status = ResearchArtifactStatus.handed_off
        bundle.content = _confirm_handoff_content(
            bundle.content, handed_off
        ).model_dump(mode="json")
        version.status = ResearchArtifactStatus.handed_off
        version.content = handed_off.model_dump(mode="json")
        project.status = ResearchArtifactStatus.handed_off
        return await _freeze_detail_and_commit(
            db,
            project.id,
            guest_id,
            idempotency_claim=idempotency.claim,
        )


def _ensure_reviewable(artifact_status: ResearchArtifactStatus) -> None:
    if artifact_status != ResearchArtifactStatus.draft:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Plan version in {artifact_status.value!r} state cannot be reviewed. "
                "Revise a reviewed plan before requesting another review."
            ),
        )


def _ensure_revisable(artifact_status: ResearchArtifactStatus) -> None:
    if artifact_status != ResearchArtifactStatus.reviewed:
        raise HTTPException(
            status_code=409,
            detail="Review this plan version before revising it.",
        )


def _validate_unique_evidence_ids(
    evidence: list[EvidenceItem],
) -> list[EvidenceItem]:
    evidence_ids = [item.id for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("evidence IDs must be unique within a request")
    return evidence


def _ensure_append_only_evidence(
    existing: list[EvidenceItem], incoming: list[EvidenceItem]
) -> None:
    existing_by_id = {item.id: item for item in existing}
    for item in incoming:
        stored = existing_by_id.get(item.id)
        if stored is None:
            continue
        if stored.model_dump(mode="json") != item.model_dump(mode="json"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Evidence ID {item.id!r} already exists with different "
                    "provenance metadata. Use a new ID for new evidence."
                ),
            )


def _ensure_review_evidence_matches_plan(
    existing: list[EvidenceItem], incoming: list[EvidenceItem]
) -> None:
    _ensure_append_only_evidence(existing, incoming)
    known_ids = {item.id for item in existing}
    new_ids = sorted(item.id for item in incoming if item.id not in known_ids)
    if new_ids:
        raise HTTPException(
            status_code=409,
            detail=(
                "Review evidence must already exist in the plan snapshot. "
                f"Add new evidence through revision first: {new_ids}."
            ),
        )


def _ensure_approvable(report: ReviewReport, plan: ResearchPlanBundle) -> None:
    required_perspectives = set(DEFAULT_REVIEWER_PERSPECTIVES)
    completed_perspectives = set(report.perspectives_completed)
    if not required_perspectives.issubset(completed_perspectives):
        missing = sorted(
            item.value for item in required_perspectives - completed_perspectives
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "The latest independent review is incomplete; missing perspectives: "
                f"{missing}."
            ),
        )
    blocking = [
        issue
        for issue in report.issues
        if issue.status == ReviewIssueStatus.OPEN
        and issue.severity in {ReviewSeverity.BLOCKER, ReviewSeverity.MAJOR}
    ]
    if report.verdict != ReviewVerdict.APPROVABLE_FOR_HANDOFF or blocking:
        raise HTTPException(
            status_code=409,
            detail="The latest independent review still contains blocker or major issues.",
        )
    if (
        plan.revision_record is not None
        and plan.revision_record.unresolved_issue_ids
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "The plan revision record still contains unresolved review issues. "
                "Revise and review the plan again before approval."
            ),
        )


def _ensure_review_targets_plan_snapshot(
    report: ReviewReport, plan: ResearchPlanBundle
) -> None:
    expected_digest = scientific_plan_digest(plan)
    if report.reviewed_plan_digest != expected_digest:
        raise HTTPException(
            status_code=409,
            detail=(
                "The independent review targets a different scientific plan "
                "snapshot. Review the current plan version again."
            ),
        )


def _ensure_expected_latest_review(
    expected_review_id: str | None, latest_review_id: str
) -> None:
    if expected_review_id is None or expected_review_id == latest_review_id:
        return
    raise HTTPException(
        status_code=409,
        detail=(
            "The requested independent review is no longer latest. Refresh and "
            "retry with the current review."
        ),
    )


def _ensure_revision_review_stable(
    *,
    selected_review_id: str,
    selected_review_round: int,
    selected_review_digest: str,
    locked_review_id: str,
    locked_review_round: int,
    locked_review_digest: str,
) -> None:
    if (
        selected_review_id == locked_review_id
        and selected_review_round == locked_review_round
        and selected_review_digest == locked_review_digest
    ):
        return
    raise HTTPException(
        status_code=409,
        detail=(
            "A newer independent review was created while the revision was being "
            "prepared. Refresh and retry."
        ),
    )


def _canonical_request_fingerprint(
    operation: str,
    request_payload: Mapping[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "operation": operation,
            "request": request_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must contain 1 to 255 non-whitespace characters.",
        )
    if any(ord(character) < 33 or ord(character) == 127 for character in normalized):
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key cannot contain whitespace or control characters.",
        )
    return normalized


def _idempotency_query(
    *,
    workspace_id: str,
    guest_id: str,
    idempotency_key: str,
):
    return select(ResearchIdempotencyReceipt).where(
        ResearchIdempotencyReceipt.workspace_id == workspace_id,
        ResearchIdempotencyReceipt.guest_id == guest_id,
        ResearchIdempotencyReceipt.idempotency_key == idempotency_key,
    )


def _receipt_claim(receipt: ResearchIdempotencyReceipt) -> _IdempotencyClaim:
    if not receipt.owner_token:
        raise HTTPException(
            status_code=500,
            detail="The idempotency receipt has no active owner token.",
        )
    return _IdempotencyClaim(
        receipt_id=receipt.id,
        owner_token=receipt.owner_token,
        workspace_id=receipt.workspace_id,
        guest_id=receipt.guest_id,
        operation=receipt.operation,
        request_fingerprint=receipt.request_fingerprint,
    )


def _validate_receipt_request(
    receipt: ResearchIdempotencyReceipt,
    *,
    operation: str,
    request_fingerprint: str,
) -> None:
    if (
        receipt.operation != operation
        or receipt.request_fingerprint != request_fingerprint
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Idempotency-Key was already used for a different operation or "
                "request payload."
            ),
        )


def _completed_receipt_response(
    receipt: ResearchIdempotencyReceipt,
) -> JSONResponse:
    if receipt.response_payload is None or receipt.response_status_code is None:
        raise HTTPException(
            status_code=500,
            detail="Completed idempotency receipt is missing its frozen response.",
        )
    try:
        detail = ResearchProjectDetail.model_validate(receipt.response_payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=500,
            detail="Stored idempotency response failed integrity validation.",
        ) from exc
    return JSONResponse(
        status_code=receipt.response_status_code,
        content=detail.model_dump(mode="json"),
    )


async def _resolve_existing_receipt(
    db: AsyncSession,
    *,
    workspace_id: str,
    guest_id: str,
    idempotency_key: str,
    operation: str,
    request_fingerprint: str,
) -> _IdempotencyResolution:
    result = await db.execute(
        _idempotency_query(
            workspace_id=workspace_id,
            guest_id=guest_id,
            idempotency_key=idempotency_key,
        ).execution_options(populate_existing=True)
    )
    receipt = result.scalar_one_or_none()
    if receipt is None:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Concurrent idempotency claim changed unexpectedly; retry.",
        )
    try:
        _validate_receipt_request(
            receipt,
            operation=operation,
            request_fingerprint=request_fingerprint,
        )
    except HTTPException:
        await db.rollback()
        raise
    if receipt.status == IDEMPOTENCY_COMPLETED:
        try:
            response = _completed_receipt_response(receipt)
        finally:
            await db.rollback()
        return _IdempotencyResolution(replay=response)
    if receipt.status != IDEMPOTENCY_IN_PROGRESS:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Unknown idempotency receipt status: {receipt.status!r}.",
        )

    now = datetime.utcnow()
    if receipt.lease_expires_at is not None and receipt.lease_expires_at > now:
        await db.rollback()
        raise HTTPException(
            status_code=425,
            detail="An identical request is already in progress.",
            headers={"Retry-After": "5"},
        )

    locked_result = await db.execute(
        _idempotency_query(
            workspace_id=workspace_id,
            guest_id=guest_id,
            idempotency_key=idempotency_key,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    locked = locked_result.scalar_one()
    try:
        _validate_receipt_request(
            locked,
            operation=operation,
            request_fingerprint=request_fingerprint,
        )
    except HTTPException:
        await db.rollback()
        raise
    if locked.status == IDEMPOTENCY_COMPLETED:
        try:
            response = _completed_receipt_response(locked)
        finally:
            await db.rollback()
        return _IdempotencyResolution(replay=response)
    now = datetime.utcnow()
    if locked.lease_expires_at is not None and locked.lease_expires_at > now:
        await db.rollback()
        raise HTTPException(
            status_code=425,
            detail="An identical request is already in progress.",
            headers={"Retry-After": "5"},
        )
    locked.owner_token = str(uuid.uuid4())
    locked.lease_expires_at = now + IDEMPOTENCY_LEASE
    locked.updated_at = now
    await db.commit()
    return _IdempotencyResolution(claim=_receipt_claim(locked))


async def _claim_idempotency(
    db: AsyncSession,
    *,
    workspace_id: str,
    guest_id: str,
    idempotency_key: str,
    operation: str,
    request_payload: Mapping[str, Any],
) -> _IdempotencyResolution:
    normalized_key = _normalize_idempotency_key(idempotency_key)
    request_fingerprint = _canonical_request_fingerprint(
        operation,
        request_payload,
    )
    existing = await db.execute(
        _idempotency_query(
            workspace_id=workspace_id,
            guest_id=guest_id,
            idempotency_key=normalized_key,
        )
    )
    receipt = existing.scalar_one_or_none()
    if receipt is not None:
        return await _resolve_existing_receipt(
            db,
            workspace_id=workspace_id,
            guest_id=guest_id,
            idempotency_key=normalized_key,
            operation=operation,
            request_fingerprint=request_fingerprint,
        )

    now = datetime.utcnow()
    receipt = ResearchIdempotencyReceipt(
        workspace_id=workspace_id,
        guest_id=guest_id,
        idempotency_key=normalized_key,
        operation=operation,
        request_fingerprint=request_fingerprint,
        status=IDEMPOTENCY_IN_PROGRESS,
        owner_token=str(uuid.uuid4()),
        lease_expires_at=now + IDEMPOTENCY_LEASE,
        response_payload=None,
    )
    db.add(receipt)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if not _is_unique_constraint_violation(exc):
            raise
        return await _resolve_existing_receipt(
            db,
            workspace_id=workspace_id,
            guest_id=guest_id,
            idempotency_key=normalized_key,
            operation=operation,
            request_fingerprint=request_fingerprint,
        )
    return _IdempotencyResolution(claim=_receipt_claim(receipt))


async def _abandon_idempotency_claim(
    db: AsyncSession,
    claim: _IdempotencyClaim,
) -> None:
    try:
        await db.rollback()
        result = await db.execute(
            select(ResearchIdempotencyReceipt)
            .where(ResearchIdempotencyReceipt.id == claim.receipt_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        receipt = result.scalar_one_or_none()
        if (
            receipt is not None
            and receipt.status == IDEMPOTENCY_IN_PROGRESS
            and receipt.owner_token == claim.owner_token
        ):
            # A failed operation has not committed domain state, so retaining
            # its key provides no replay value and lets invalid unique keys
            # grow this table without bound. The owner/status fence makes the
            # delete safe even when a replacement worker has taken over.
            await db.delete(receipt)
            await db.commit()
        else:
            await db.rollback()
    except BaseException:
        await db.rollback()


async def _renew_idempotency_lease(claim: _IdempotencyClaim) -> None:
    """Keep a live worker's short lease current without spanning the LLM call."""

    while True:
        await asyncio.sleep(IDEMPOTENCY_HEARTBEAT_SECONDS)
        now = datetime.utcnow()
        try:
            async with AsyncSessionLocal() as heartbeat_db:
                result = await heartbeat_db.execute(
                    update(ResearchIdempotencyReceipt)
                    .where(
                        ResearchIdempotencyReceipt.id == claim.receipt_id,
                        ResearchIdempotencyReceipt.status == IDEMPOTENCY_IN_PROGRESS,
                        ResearchIdempotencyReceipt.owner_token == claim.owner_token,
                    )
                    .values(
                        lease_expires_at=now + IDEMPOTENCY_LEASE,
                        updated_at=now,
                    )
                )
                await heartbeat_db.commit()
                if result.rowcount != 1:
                    return
        except Exception:
            # The current lease remains the safety boundary. A later heartbeat
            # can recover from a transient connection failure; a dead worker is
            # deliberately recoverable once the short lease expires.
            continue


async def _stop_idempotency_heartbeat(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@asynccontextmanager
async def _idempotency_guard(
    db: AsyncSession,
    *,
    workspace_id: str,
    guest_id: str,
    idempotency_key: str,
    operation: str,
    request_payload: Mapping[str, Any],
) -> AsyncIterator[_IdempotencyResolution]:
    resolution = await _claim_idempotency(
        db,
        workspace_id=workspace_id,
        guest_id=guest_id,
        idempotency_key=idempotency_key,
        operation=operation,
        request_payload=request_payload,
    )
    heartbeat = (
        asyncio.create_task(_renew_idempotency_lease(resolution.claim))
        if resolution.claim is not None
        else None
    )
    try:
        yield resolution
    except BaseException:
        await _stop_idempotency_heartbeat(heartbeat)
        if resolution.claim is not None:
            await _abandon_idempotency_claim(db, resolution.claim)
        raise
    else:
        await _stop_idempotency_heartbeat(heartbeat)


def _enforce_fresh_llm_rate_limit(request: Request, operation: str) -> None:
    scope = _FRESH_RATE_SCOPES[operation]
    # SlowAPI's public decorator charges before endpoint execution. Calling its
    # registered scope after a durable fresh claim exempts completed replays.
    limiter._check_request_limit(request, scope, in_middleware=False)


async def _commit_or_conflict(db: AsyncSession, detail: str) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_unique_constraint_violation(exc):
            raise HTTPException(status_code=409, detail=detail) from exc
        raise


async def _freeze_detail_and_commit(
    db: AsyncSession,
    project_id: str,
    guest_id: str,
    *,
    conflict_detail: str | None = None,
    idempotency_claim: _IdempotencyClaim | None = None,
    response_status_code: int = status.HTTP_200_OK,
) -> ResearchProjectDetail:
    """Build the mutation response before releasing its transaction locks."""

    try:
        await db.flush()
        detail = await _project_detail(db, project_id, guest_id)
        if idempotency_claim is not None:
            receipt_result = await db.execute(
                select(ResearchIdempotencyReceipt)
                .where(
                    ResearchIdempotencyReceipt.id
                    == idempotency_claim.receipt_id
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            receipt = receipt_result.scalar_one_or_none()
            if (
                receipt is None
                or receipt.status != IDEMPOTENCY_IN_PROGRESS
                or receipt.owner_token != idempotency_claim.owner_token
                or receipt.operation != idempotency_claim.operation
                or receipt.request_fingerprint
                != idempotency_claim.request_fingerprint
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The idempotency claim expired or changed before this "
                        "operation could commit. Retry with the same key."
                    ),
                )
            now = datetime.utcnow()
            receipt.status = IDEMPOTENCY_COMPLETED
            receipt.response_status_code = response_status_code
            receipt.response_payload = detail.model_dump(mode="json")
            receipt.owner_token = None
            receipt.lease_expires_at = None
            receipt.completed_at = now
            receipt.updated_at = now
            await db.flush()
        await db.commit()
        return detail
    except IntegrityError as exc:
        await db.rollback()
        if conflict_detail and _is_unique_constraint_violation(exc):
            raise HTTPException(status_code=409, detail=conflict_detail) from exc
        raise


def _is_unique_constraint_violation(exc: IntegrityError) -> bool:
    pending: list[BaseException] = [exc]
    visited: set[int] = set()
    messages: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        messages.append(str(current).lower())
        for code_attribute in ("sqlstate", "pgcode"):
            if getattr(current, code_attribute, None) == "23505":
                return True
        for nested in (
            getattr(current, "orig", None),
            current.__cause__,
            current.__context__,
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    message = " ".join(messages)
    return "unique constraint" in message or "duplicate key" in message


def _mark_plan_handed_off(plan: ResearchPlanBundle) -> ResearchPlanBundle:
    if plan.lifecycle_status != PlanLifecycleStatus.APPROVED_FOR_HANDOFF:
        raise ValueError("only an approved plan can be marked as handed off")
    payload = plan.model_dump(mode="json")
    payload["lifecycle_status"] = PlanLifecycleStatus.HANDED_OFF.value
    payload["implementation_plan"]["handoff"]["handoff_status"] = "handed_off"
    return ResearchPlanBundle.model_validate(payload)


def _build_prepared_handoff_content(
    *,
    bundle_id: str,
    project_id: str,
    plan_version_id: str,
    plan: ResearchPlanBundle,
    review: ReviewReport,
) -> ResearchHandoffContent:
    if plan.lifecycle_status != PlanLifecycleStatus.APPROVED_FOR_HANDOFF:
        raise ValueError("handoff preparation requires an approved plan snapshot")
    plan_snapshot = plan.model_dump(mode="json")
    return ResearchHandoffContent(
        schema_version="1.0",
        bundle_id=bundle_id,
        project_id=project_id,
        plan_version_id=plan_version_id,
        plan_version_number=plan.version,
        status="ready_for_handoff",
        execution_status="awaiting_external_execution",
        plan_snapshot=plan_snapshot,
        research_contract=plan.contract,
        hypotheses=plan.hypotheses,
        methods=plan.methods,
        experiment_plans=plan.experiments,
        implementation_plan=plan.implementation_plan,
        independent_review=review,
        boundary=(
            "PaperPilot prepared this package, but external transfer is not yet "
            "confirmed. PaperPilot did not execute code, builds, tests, or experiments."
        ),
    )


def _confirm_handoff_content(
    prepared_content: ResearchHandoffContent | Mapping[str, Any],
    handed_off_plan: ResearchPlanBundle,
) -> ResearchHandoffContent:
    prepared = (
        prepared_content
        if isinstance(prepared_content, ResearchHandoffContent)
        else _load_persisted_handoff(prepared_content)
    )
    if prepared.status != "ready_for_handoff":
        raise ValueError("only a ready handoff bundle can be confirmed")
    if handed_off_plan.lifecycle_status != PlanLifecycleStatus.HANDED_OFF:
        raise ValueError("handoff confirmation requires a handed-off plan snapshot")
    confirmed = prepared.model_dump(mode="json")
    confirmed.update(
        {
            "status": "handed_off",
            "plan_snapshot": handed_off_plan.model_dump(mode="json"),
            "implementation_plan": handed_off_plan.implementation_plan.model_dump(
                mode="json"
            ),
            "boundary": (
                "External transfer was explicitly confirmed. PaperPilot did not "
                "execute code, builds, tests, or experiments; returned results "
                "still require separate verification."
            ),
        }
    )
    return ResearchHandoffContent.model_validate(confirmed)


async def _get_owned_workspace(
    db: AsyncSession, workspace_id: str, guest_id: str
) -> Workspace:
    result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.guest_id == guest_id,
        )
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return workspace


async def _get_owned_project(
    db: AsyncSession, project_id: str, guest_id: str
) -> ResearchProject:
    result = await db.execute(
        select(ResearchProject).where(
            ResearchProject.id == project_id,
            ResearchProject.guest_id == guest_id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Research project not found.")
    return project


async def _get_owned_version(
    db: AsyncSession,
    project_id: str,
    version_number: int,
    guest_id: str,
    *,
    for_update: bool = False,
) -> tuple[ResearchProject, ResearchPlanVersion]:
    project = await _get_owned_project(db, project_id, guest_id)
    query = _owned_version_query(
        project_id=project.id,
        version_number=version_number,
        guest_id=guest_id,
        for_update=for_update,
    )
    result = await db.execute(query)
    version = result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="Research plan version not found.")
    return project, version


def _owned_version_query(
    *,
    project_id: str,
    version_number: int,
    guest_id: str,
    for_update: bool,
):
    query = select(ResearchPlanVersion).where(
        ResearchPlanVersion.research_project_id == project_id,
        ResearchPlanVersion.version_number == version_number,
        ResearchPlanVersion.guest_id == guest_id,
    )
    if for_update:
        query = query.execution_options(populate_existing=True).with_for_update()
    return query


async def _select_review(
    db: AsyncSession,
    plan_version_id: str,
    review_id: str | None,
    guest_id: str,
) -> ResearchPlanReview:
    query = select(ResearchPlanReview).where(
        ResearchPlanReview.research_plan_version_id == plan_version_id,
        ResearchPlanReview.guest_id == guest_id,
    )
    if review_id:
        query = query.where(ResearchPlanReview.id == review_id)
    else:
        query = query.order_by(ResearchPlanReview.review_round.desc()).limit(1)
    result = await db.execute(query)
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Independent review not found.")
    return review


async def _select_handoff_bundle(
    db: AsyncSession,
    *,
    project_id: str,
    version_number: int,
    guest_id: str,
    for_update: bool = False,
) -> ResearchHandoffBundle | None:
    query = select(ResearchHandoffBundle).where(
        ResearchHandoffBundle.research_project_id == project_id,
        ResearchHandoffBundle.version_number == version_number,
        ResearchHandoffBundle.guest_id == guest_id,
    )
    if for_update:
        query = query.with_for_update()
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def _project_detail(
    db: AsyncSession, project_id: str, guest_id: str
) -> ResearchProjectDetail:
    result = await db.execute(
        select(ResearchProject)
        .options(
            selectinload(ResearchProject.plan_versions),
            selectinload(ResearchProject.reviews),
            selectinload(ResearchProject.handoff_bundles),
        )
        .where(
            ResearchProject.id == project_id,
            ResearchProject.guest_id == guest_id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Research project not found.")
    versions = sorted(project.plan_versions, key=lambda item: item.version_number)
    try:
        return ResearchProjectDetail(
            project=_summary(
                project,
                versions[-1].version_number if versions else None,
            ),
            plan_versions=[
                ResearchPlanVersionOut(
                    id=item.id,
                    version_number=item.version_number,
                    status=item.status,
                    content=item.content,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
                for item in versions
            ],
            reviews=[
                ResearchPlanReviewOut(
                    id=item.id,
                    plan_version_id=item.research_plan_version_id,
                    review_round=item.review_round,
                    status=item.status,
                    review=item.review,
                    created_at=item.created_at,
                )
                for item in sorted(
                    project.reviews, key=lambda value: value.created_at
                )
            ],
            handoff_bundles=[
                ResearchHandoffOut(
                    id=item.id,
                    plan_version_id=item.research_plan_version_id,
                    version_number=item.version_number,
                    status=item.status,
                    content=item.content,
                    created_at=item.created_at,
                )
                for item in sorted(
                    project.handoff_bundles,
                    key=lambda value: value.version_number,
                )
            ],
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=500,
            detail="Stored Research Director artifacts failed integrity validation.",
        ) from exc


def _summary(
    project: ResearchProject, latest_version_number: int | None
) -> ResearchProjectSummary:
    try:
        return ResearchProjectSummary(
            id=project.id,
            workspace_id=project.workspace_id,
            title=project.title,
            objective=project.objective,
            brief_snapshot=ResearchBriefSnapshot.model_validate(
                (project.content or {}).get("brief_snapshot")
            )
            if (project.content or {}).get("brief_snapshot") is not None
            else None,
            status=project.status,
            latest_version_number=latest_version_number,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=500,
            detail="Stored research project summary failed integrity validation.",
        ) from exc
