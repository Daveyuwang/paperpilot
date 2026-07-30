from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _DomainModel(BaseModel):
    """Shared validation policy for Research Director domain objects."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class PlanLifecycleStatus(str, Enum):
    """Lifecycle states that remain strictly before external execution."""

    DRAFT = "draft"
    REVIEW_REQUIRED = "review_required"
    APPROVED_FOR_HANDOFF = "approved_for_handoff"
    HANDED_OFF = "handed_off"


class GenerationMode(str, Enum):
    MODEL = "model"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class EvidenceRelation(str, Enum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    CONFLICTS = "conflicts"
    UNKNOWN = "unknown"


class EvidenceStatus(str, Enum):
    SUPPORTED = "supported"
    CONTESTED = "contested"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    EVIDENCE_BACKED = "evidence_backed"


class ReviewerPerspective(str, Enum):
    EVIDENCE = "evidence"
    NOVELTY = "novelty"
    METHOD = "method"
    EXPERIMENT = "experiment"
    STATISTICS = "statistics"
    IMPLEMENTATION = "implementation"
    RISK = "risk"
    BOUNDARY = "execution_boundary"


DEFAULT_REVIEWER_PERSPECTIVES: tuple[ReviewerPerspective, ...] = (
    ReviewerPerspective.EVIDENCE,
    ReviewerPerspective.NOVELTY,
    ReviewerPerspective.METHOD,
    ReviewerPerspective.EXPERIMENT,
    ReviewerPerspective.STATISTICS,
    ReviewerPerspective.IMPLEMENTATION,
    ReviewerPerspective.RISK,
    ReviewerPerspective.BOUNDARY,
)


class ReviewSeverity(str, Enum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"


class ReviewIssueStatus(str, Enum):
    OPEN = "open"
    ADDRESSED = "addressed"
    ACCEPTED_RISK = "accepted_risk"


class ReviewVerdict(str, Enum):
    BLOCKED = "blocked"
    REVISION_REQUIRED = "revision_required"
    APPROVABLE_FOR_HANDOFF = "approvable_for_handoff"


class ReviewState(str, Enum):
    INDEPENDENT_REVIEW_COMPLETE = "independent_review_complete"
    FALLBACK_REQUIRES_HUMAN_REVIEW = "fallback_requires_human_review"


class EvidenceItem(_DomainModel):
    """A caller-provided source or passage; its contents are untrusted data."""

    id: NonEmptyStr
    title: NonEmptyStr
    source_uri: str | None = None
    source_type: str = "other"
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=0)
    excerpt: str | None = None
    summary: str | None = None
    locator: str | None = None

    @model_validator(mode="after")
    def require_source_content_or_location(self) -> EvidenceItem:
        if not any((self.source_uri, self.excerpt, self.summary)):
            raise ValueError("evidence item requires source_uri, excerpt, or summary")
        return self


class ResearchContract(_DomainModel):
    title: NonEmptyStr
    research_question: NonEmptyStr
    objective: NonEmptyStr
    scope_inclusions: list[NonEmptyStr] = Field(min_length=1)
    scope_exclusions: list[NonEmptyStr] = Field(default_factory=list)
    constraints: list[NonEmptyStr] = Field(default_factory=list)
    assumptions: list[NonEmptyStr] = Field(default_factory=list)
    unknowns: list[NonEmptyStr] = Field(default_factory=list)
    success_criteria: list[NonEmptyStr] = Field(min_length=1)
    failure_criteria: list[NonEmptyStr] = Field(default_factory=list)
    allowed_sources: list[NonEmptyStr] = Field(default_factory=list)
    excluded_sources: list[NonEmptyStr] = Field(default_factory=list)
    required_deliverables: list[NonEmptyStr] = Field(min_length=1)
    human_decisions_required: list[NonEmptyStr] = Field(default_factory=list)


class ResearchContractSeed(_DomainModel):
    """Optional caller-owned contract fields that the planner may not rewrite."""

    title: NonEmptyStr | None = None
    research_question: NonEmptyStr | None = None
    objective: NonEmptyStr | None = None
    scope_inclusions: list[NonEmptyStr] | None = Field(default=None, min_length=1)
    scope_exclusions: list[NonEmptyStr] | None = None
    constraints: list[NonEmptyStr] | None = None
    assumptions: list[NonEmptyStr] | None = None
    unknowns: list[NonEmptyStr] | None = None
    success_criteria: list[NonEmptyStr] | None = Field(default=None, min_length=1)
    failure_criteria: list[NonEmptyStr] | None = None
    allowed_sources: list[NonEmptyStr] | None = None
    excluded_sources: list[NonEmptyStr] | None = None
    required_deliverables: list[NonEmptyStr] | None = Field(
        default=None, min_length=1
    )
    human_decisions_required: list[NonEmptyStr] | None = None


class EvidenceClaim(_DomainModel):
    id: NonEmptyStr
    statement: NonEmptyStr
    evidence_item_ids: list[NonEmptyStr] = Field(default_factory=list)
    relation: EvidenceRelation = EvidenceRelation.UNKNOWN
    status: EvidenceStatus = EvidenceStatus.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    limitations: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def supported_claims_need_evidence(self) -> EvidenceClaim:
        if self.status == EvidenceStatus.SUPPORTED:
            if not self.evidence_item_ids:
                raise ValueError("supported claims require evidence_item_ids")
            if self.relation != EvidenceRelation.SUPPORTS:
                raise ValueError("supported claims require relation='supports'")
        elif self.status == EvidenceStatus.CONTESTED:
            if not self.evidence_item_ids:
                raise ValueError("contested claims require evidence_item_ids")
            if self.relation != EvidenceRelation.CONFLICTS:
                raise ValueError("contested claims require relation='conflicts'")
        return self


class ResearchGap(_DomainModel):
    id: NonEmptyStr
    description: NonEmptyStr
    evidence_claim_ids: list[NonEmptyStr] = Field(default_factory=list)
    contrary_claim_ids: list[NonEmptyStr] = Field(default_factory=list)
    impact: NonEmptyStr
    testability: NonEmptyStr
    novelty_assessment: NonEmptyStr
    novelty_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertainties: list[NonEmptyStr] = Field(default_factory=list)


class ResearchHypothesis(_DomainModel):
    id: NonEmptyStr
    statement: NonEmptyStr
    rationale: NonEmptyStr
    evidence_claim_ids: list[NonEmptyStr] = Field(default_factory=list)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    falsifiable_predictions: list[NonEmptyStr] = Field(min_length=1)
    differentiation_from_prior_work: NonEmptyStr
    strongest_counterargument: NonEmptyStr
    minimum_validation: list[NonEmptyStr] = Field(min_length=1)
    dependencies: list[NonEmptyStr] = Field(default_factory=list)
    risks: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_backed_hypotheses_need_claims(self) -> ResearchHypothesis:
        if self.status == HypothesisStatus.EVIDENCE_BACKED and not self.evidence_claim_ids:
            raise ValueError("evidence-backed hypotheses require evidence_claim_ids")
        return self


class AlternativeMethod(_DomainModel):
    title: NonEmptyStr
    description: NonEmptyStr
    rejection_reason: NonEmptyStr
    reconsider_when: str | None = None


class MethodSpec(_DomainModel):
    id: NonEmptyStr
    title: NonEmptyStr
    summary: NonEmptyStr
    hypothesis_ids: list[NonEmptyStr] = Field(min_length=1)
    components: list[NonEmptyStr] = Field(min_length=1)
    procedure: list[NonEmptyStr] = Field(min_length=1)
    interfaces_or_boundaries: list[NonEmptyStr] = Field(default_factory=list)
    assumptions: list[NonEmptyStr] = Field(default_factory=list)
    alternatives_considered: list[AlternativeMethod] = Field(default_factory=list)
    selection_rationale: NonEmptyStr
    risks: list[NonEmptyStr] = Field(default_factory=list)


class DatasetSpec(_DomainModel):
    name: NonEmptyStr
    purpose: NonEmptyStr
    split_or_sampling: NonEmptyStr
    access_or_license_notes: str | None = None
    leakage_checks: list[NonEmptyStr] = Field(default_factory=list)


class MetricSpec(_DomainModel):
    name: NonEmptyStr
    definition: NonEmptyStr
    direction: Literal["higher_is_better", "lower_is_better", "target_range"]
    success_threshold: str | None = None


class ExperimentPlan(_DomainModel):
    id: NonEmptyStr
    title: NonEmptyStr
    research_question: NonEmptyStr
    hypothesis_ids: list[NonEmptyStr] = Field(min_length=1)
    method_id: NonEmptyStr
    datasets: list[DatasetSpec] = Field(min_length=1)
    baselines: list[NonEmptyStr] = Field(min_length=1)
    metrics: list[MetricSpec] = Field(min_length=1)
    controls: list[NonEmptyStr] = Field(min_length=1)
    ablations: list[NonEmptyStr] = Field(default_factory=list)
    negative_tests: list[NonEmptyStr] = Field(default_factory=list)
    statistical_analysis: NonEmptyStr
    seeds_or_repetitions: NonEmptyStr
    stop_conditions: list[NonEmptyStr] = Field(min_length=1)
    expected_artifacts: list[NonEmptyStr] = Field(min_length=1)
    acceptance_criteria: list[NonEmptyStr] = Field(min_length=1)
    risks: list[NonEmptyStr] = Field(default_factory=list)
    execution_status: Literal["awaiting_external_execution"] = (
        "awaiting_external_execution"
    )


class InterfaceContract(_DomainModel):
    name: NonEmptyStr
    inputs: list[NonEmptyStr] = Field(default_factory=list)
    outputs: list[NonEmptyStr] = Field(default_factory=list)
    invariants: list[NonEmptyStr] = Field(default_factory=list)


class WorkPackage(_DomainModel):
    id: NonEmptyStr
    title: NonEmptyStr
    objective: NonEmptyStr
    tasks: list[NonEmptyStr] = Field(min_length=1)
    inputs: list[NonEmptyStr] = Field(default_factory=list)
    outputs: list[NonEmptyStr] = Field(min_length=1)
    interface_contracts: list[InterfaceContract] = Field(default_factory=list)
    acceptance_criteria: list[NonEmptyStr] = Field(min_length=1)
    dependency_ids: list[NonEmptyStr] = Field(default_factory=list)
    owner_role: NonEmptyStr
    effort_estimate: str | None = None
    risks: list[NonEmptyStr] = Field(default_factory=list)
    execution_status: Literal["planned_for_external_execution"] = (
        "planned_for_external_execution"
    )


class Milestone(_DomainModel):
    name: NonEmptyStr
    work_package_ids: list[NonEmptyStr] = Field(min_length=1)
    exit_criteria: list[NonEmptyStr] = Field(min_length=1)


class HandoffSpecification(_DomainModel):
    target_roles: list[NonEmptyStr] = Field(min_length=1)
    prerequisites: list[NonEmptyStr] = Field(default_factory=list)
    included_artifacts: list[NonEmptyStr] = Field(min_length=1)
    execution_instructions: list[NonEmptyStr] = Field(min_length=1)
    external_result_contract: list[NonEmptyStr] = Field(min_length=1)
    human_approval_required: bool = True
    handoff_status: Literal["not_handed_off", "handed_off"] = "not_handed_off"


class ImplementationPlan(_DomainModel):
    objective: NonEmptyStr
    architecture_or_method_summary: NonEmptyStr
    work_packages: list[WorkPackage] = Field(min_length=1)
    milestones: list[Milestone] = Field(min_length=1)
    unresolved_decisions: list[NonEmptyStr] = Field(default_factory=list)
    resource_assumptions: list[NonEmptyStr] = Field(default_factory=list)
    fallback_strategies: list[NonEmptyStr] = Field(default_factory=list)
    handoff: HandoffSpecification
    execution_status: Literal["awaiting_external_execution"] = (
        "awaiting_external_execution"
    )

    @model_validator(mode="after")
    def validate_work_package_graph(self) -> ImplementationPlan:
        work_package_ids = [item.id for item in self.work_packages]
        _require_unique(work_package_ids, "work package IDs")
        known = set(work_package_ids)
        dependency_graph: dict[str, set[str]] = {}
        for item in self.work_packages:
            _require_unique(item.dependency_ids, f"work package {item.id!r} dependencies")
            unknown = set(item.dependency_ids) - known
            if unknown:
                raise ValueError(
                    f"work package {item.id!r} has unknown dependencies: {sorted(unknown)}"
                )
            if item.id in item.dependency_ids:
                raise ValueError(f"work package {item.id!r} cannot depend on itself")
            dependency_graph[item.id] = set(item.dependency_ids)
        if _contains_dependency_cycle(dependency_graph):
            raise ValueError("work package dependencies must form an acyclic graph")
        _require_unique([item.name for item in self.milestones], "milestone names")
        for milestone in self.milestones:
            _require_unique(
                milestone.work_package_ids,
                f"milestone {milestone.name!r} work packages",
            )
            unknown = set(milestone.work_package_ids) - known
            if unknown:
                raise ValueError(
                    f"milestone {milestone.name!r} has unknown work packages: {sorted(unknown)}"
                )
        return self


class RevisionRecord(_DomainModel):
    review_id: NonEmptyStr
    source_plan_digest: NonEmptyStr | None = None
    source_review_digest: NonEmptyStr | None = None
    addressed_issue_ids: list[NonEmptyStr] = Field(default_factory=list)
    unresolved_issue_ids: list[NonEmptyStr] = Field(default_factory=list)
    changes: list[NonEmptyStr] = Field(min_length=1)

    @model_validator(mode="after")
    def issue_sets_must_not_overlap(self) -> RevisionRecord:
        overlap = set(self.addressed_issue_ids) & set(self.unresolved_issue_ids)
        if overlap:
            raise ValueError(f"issue IDs cannot be both addressed and unresolved: {sorted(overlap)}")
        return self


class ResearchPlanBundle(_DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    plan_id: NonEmptyStr
    version: int = Field(default=1, ge=1)
    supersedes_plan_id: str | None = None
    lifecycle_status: PlanLifecycleStatus = PlanLifecycleStatus.DRAFT
    generation_mode: GenerationMode = GenerationMode.MODEL
    contract: ResearchContract
    evidence_catalog: list[EvidenceItem] = Field(default_factory=list)
    evidence_claims: list[EvidenceClaim] = Field(default_factory=list)
    gaps: list[ResearchGap] = Field(min_length=1)
    hypotheses: list[ResearchHypothesis] = Field(min_length=1)
    methods: list[MethodSpec] = Field(min_length=1)
    experiments: list[ExperimentPlan] = Field(min_length=1)
    implementation_plan: ImplementationPlan
    limitations: list[NonEmptyStr] = Field(default_factory=list)
    unresolved_questions: list[NonEmptyStr] = Field(default_factory=list)
    generation_warnings: list[NonEmptyStr] = Field(default_factory=list)
    revision_record: RevisionRecord | None = None

    @model_validator(mode="after")
    def validate_cross_references(self) -> ResearchPlanBundle:
        evidence_ids = [item.id for item in self.evidence_catalog]
        claim_ids = [item.id for item in self.evidence_claims]
        gap_ids = [item.id for item in self.gaps]
        hypothesis_ids = [item.id for item in self.hypotheses]
        method_ids = [item.id for item in self.methods]
        experiment_ids = [item.id for item in self.experiments]
        for label, values in (
            ("evidence item IDs", evidence_ids),
            ("claim IDs", claim_ids),
            ("gap IDs", gap_ids),
            ("hypothesis IDs", hypothesis_ids),
            ("method IDs", method_ids),
            ("experiment IDs", experiment_ids),
        ):
            _require_unique(values, label)

        known_evidence = set(evidence_ids)
        evidence_by_id = {item.id: item for item in self.evidence_catalog}
        for claim in self.evidence_claims:
            _require_known(
                claim.evidence_item_ids,
                known_evidence,
                f"claim {claim.id!r} evidence items",
            )
            if claim.status == EvidenceStatus.SUPPORTED:
                # Multiple references are allowed, but at least one must be a
                # decisive, frozen passage. Metadata or a generated summary is
                # useful for discovery, not sufficient support for a claim.
                has_located_passage = any(
                    _has_decisive_located_passage(evidence_by_id[item_id])
                    for item_id in claim.evidence_item_ids
                )
                if not has_located_passage:
                    raise ValueError(
                        f"supported claim {claim.id!r} requires at least one "
                        "evidence item with a non-empty excerpt and concrete locator; "
                        "URL, title, or summary metadata alone is not support"
                    )

        known_claims = set(claim_ids)
        claims_by_id = {item.id: item for item in self.evidence_claims}
        for gap in self.gaps:
            _require_known(gap.evidence_claim_ids, known_claims, f"gap {gap.id!r} claims")
            _require_known(gap.contrary_claim_ids, known_claims, f"gap {gap.id!r} contrary claims")
        for hypothesis in self.hypotheses:
            _require_known(
                hypothesis.evidence_claim_ids,
                known_claims,
                f"hypothesis {hypothesis.id!r} claims",
            )
            if hypothesis.status == HypothesisStatus.EVIDENCE_BACKED:
                supported_claims = [
                    claims_by_id[claim_id]
                    for claim_id in hypothesis.evidence_claim_ids
                    if claims_by_id[claim_id].status == EvidenceStatus.SUPPORTED
                ]
                if not supported_claims:
                    raise ValueError(
                        f"evidence-backed hypothesis {hypothesis.id!r} requires "
                        "at least one supported claim"
                    )

        known_hypotheses = set(hypothesis_ids)
        for method in self.methods:
            _require_known(
                method.hypothesis_ids,
                known_hypotheses,
                f"method {method.id!r} hypotheses",
            )

        known_methods = set(method_ids)
        for experiment in self.experiments:
            _require_known(
                experiment.hypothesis_ids,
                known_hypotheses,
                f"experiment {experiment.id!r} hypotheses",
            )
            _require_known(
                [experiment.method_id],
                known_methods,
                f"experiment {experiment.id!r} method",
            )

        nested_handoff_status = self.implementation_plan.handoff.handoff_status
        if self.lifecycle_status == PlanLifecycleStatus.HANDED_OFF:
            if nested_handoff_status != "handed_off":
                raise ValueError(
                    "handed-off plans require implementation_plan.handoff.handoff_status="
                    "'handed_off'"
                )
        elif nested_handoff_status != "not_handed_off":
            raise ValueError(
                "plans cannot claim a nested handoff before lifecycle_status='handed_off'"
            )
        return self


class ReviewIssue(_DomainModel):
    id: NonEmptyStr
    perspective: ReviewerPerspective
    severity: ReviewSeverity
    artifact_path: NonEmptyStr
    problem: NonEmptyStr
    evidence: NonEmptyStr
    impact: NonEmptyStr
    required_fix: NonEmptyStr
    status: ReviewIssueStatus = ReviewIssueStatus.OPEN


class ReviewReport(_DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    review_id: NonEmptyStr
    reviewed_plan_id: NonEmptyStr
    reviewed_plan_version: int = Field(ge=1)
    reviewed_plan_digest: NonEmptyStr | None = None
    review_state: ReviewState = ReviewState.INDEPENDENT_REVIEW_COMPLETE
    verdict: ReviewVerdict
    perspectives_completed: list[ReviewerPerspective] = Field(default_factory=list)
    issues: list[ReviewIssue] = Field(default_factory=list)
    strengths: list[NonEmptyStr] = Field(default_factory=list)
    summary: NonEmptyStr
    required_next_step: NonEmptyStr

    @model_validator(mode="after")
    def normalize_verdict_and_issue_state(self) -> ReviewReport:
        _require_unique([item.id for item in self.issues], "review issue IDs")
        if self.review_state == ReviewState.FALLBACK_REQUIRES_HUMAN_REVIEW:
            expected = ReviewVerdict.BLOCKED
        elif any(item.severity == ReviewSeverity.BLOCKER for item in self.issues):
            expected = ReviewVerdict.BLOCKED
        elif any(item.severity == ReviewSeverity.MAJOR for item in self.issues):
            expected = ReviewVerdict.REVISION_REQUIRED
        else:
            expected = ReviewVerdict.APPROVABLE_FOR_HANDOFF
        # Avoid validate_assignment recursively invoking this model validator.
        object.__setattr__(self, "verdict", expected)
        return self


class GeneratePlanRequest(_DomainModel):
    research_brief: NonEmptyStr
    contract: ResearchContractSeed | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    evidence_warnings: list[NonEmptyStr] = Field(default_factory=list)
    constraints: list[NonEmptyStr] = Field(default_factory=list)
    desired_deliverables: list[NonEmptyStr] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("evidence")
    @classmethod
    def evidence_ids_must_be_unique(cls, value: list[EvidenceItem]) -> list[EvidenceItem]:
        _require_unique([item.id for item in value], "request evidence IDs")
        return value


class ReviewPlanRequest(_DomainModel):
    plan: ResearchPlanBundle
    evidence: list[EvidenceItem] = Field(default_factory=list)
    perspectives: list[ReviewerPerspective] = Field(
        default_factory=lambda: list(DEFAULT_REVIEWER_PERSPECTIVES),
        min_length=1,
    )
    review_instructions: str | None = None

    @field_validator("evidence")
    @classmethod
    def evidence_ids_must_be_unique(
        cls, value: list[EvidenceItem]
    ) -> list[EvidenceItem]:
        _require_unique([item.id for item in value], "review evidence IDs")
        return value

    @field_validator("perspectives")
    @classmethod
    def perspectives_must_be_unique(
        cls, value: list[ReviewerPerspective]
    ) -> list[ReviewerPerspective]:
        _require_unique([item.value for item in value], "reviewer perspectives")
        return value

    @model_validator(mode="after")
    def evidence_must_not_override_plan_catalog(self) -> ReviewPlanRequest:
        _require_append_only_evidence(self.plan.evidence_catalog, self.evidence)
        known_ids = {item.id for item in self.plan.evidence_catalog}
        new_ids = sorted(item.id for item in self.evidence if item.id not in known_ids)
        if new_ids:
            raise ValueError(
                "review evidence must already exist in the plan catalog; "
                f"new IDs require revision: {new_ids}"
            )
        return self


class RevisePlanRequest(_DomainModel):
    plan: ResearchPlanBundle
    review: ReviewReport
    evidence: list[EvidenceItem] = Field(default_factory=list)
    evidence_warnings: list[NonEmptyStr] = Field(default_factory=list)
    revision_instructions: str | None = None

    @field_validator("evidence")
    @classmethod
    def evidence_ids_must_be_unique(
        cls, value: list[EvidenceItem]
    ) -> list[EvidenceItem]:
        _require_unique([item.id for item in value], "revision evidence IDs")
        return value

    @model_validator(mode="after")
    def review_must_target_plan(self) -> RevisePlanRequest:
        if self.review.reviewed_plan_id != self.plan.plan_id:
            raise ValueError("review.reviewed_plan_id must match plan.plan_id")
        if self.review.reviewed_plan_version != self.plan.version:
            raise ValueError("review.reviewed_plan_version must match plan.version")
        expected_digest = scientific_plan_digest(self.plan)
        if self.review.reviewed_plan_digest != expected_digest:
            raise ValueError(
                "review.reviewed_plan_digest must match the scientific plan snapshot"
            )
        _require_append_only_evidence(self.plan.evidence_catalog, self.evidence)
        return self


def _require_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _require_known(values: list[str], known: set[str], label: str) -> None:
    unknown = set(values) - known
    if unknown:
        raise ValueError(f"{label} contain unknown IDs: {sorted(unknown)}")


def _has_decisive_located_passage(item: EvidenceItem) -> bool:
    """Return whether an evidence item can decisively support a claim.

    A concrete locator may be a page, section, paragraph, line range, chunk ID,
    or other stable source-local address. Empty and conventional placeholder
    values are never concrete.
    """

    if not item.excerpt or not item.locator:
        return False
    placeholder_locators = {
        "-",
        "n/a",
        "na",
        "none",
        "not provided",
        "tbd",
        "unknown",
        "unspecified",
    }
    return item.locator.casefold() not in placeholder_locators


def _require_append_only_evidence(
    existing: list[EvidenceItem], incoming: list[EvidenceItem]
) -> None:
    existing_by_id = {item.id: item for item in existing}
    for item in incoming:
        stored = existing_by_id.get(item.id)
        if stored is None:
            continue
        if stored.model_dump(mode="json") != item.model_dump(mode="json"):
            raise ValueError(
                f"evidence ID {item.id!r} cannot overwrite existing provenance"
            )


def _contains_dependency_cycle(graph: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(dependency) for dependency in graph[node]):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def scientific_plan_digest(plan: ResearchPlanBundle) -> str:
    """Hash scientific/provenance content while ignoring handoff lifecycle markers."""

    payload = plan.model_dump(mode="json")
    payload.pop("lifecycle_status", None)
    handoff = payload.get("implementation_plan", {}).get("handoff", {})
    if isinstance(handoff, dict):
        handoff.pop("handoff_status", None)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def review_report_digest(report: ReviewReport) -> str:
    canonical = json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
