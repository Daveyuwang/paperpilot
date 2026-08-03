from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SubQuestion(BaseModel):
    id: str = Field(description="Unique identifier for this sub-question")
    question: str = Field(description="The research sub-question to investigate")
    search_queries: list[str] = Field(
        description="1-3 search queries to answer this sub-question",
    )
    priority: int = Field(default=1, ge=1, description="Priority rank (1=highest)")
    rationale: str = Field(description="Why this sub-question matters for the overall topic")


class Plan(BaseModel):
    sub_questions: list[SubQuestion] = Field(
        description="List of 3-8 sub-questions to investigate",
    )
    overall_approach: str = Field(description="Brief description of the research approach")


class RepairPlan(BaseModel):
    sub_questions: list[SubQuestion] = Field(
        min_length=1,
        max_length=8,
        description="One to eight replacement sub-questions for a bounded repair",
    )
    overall_approach: str = Field(description="Brief description of the repair approach")


class SourceRef(BaseModel):
    source_id: str = Field(
        default="",
        max_length=64,
        description="Stable source identifier when available",
    )
    url: str = Field(default="", description="URL of the source")
    title: str = Field(default="", max_length=500, description="Title of the source")
    excerpt: str = Field(
        default="",
        max_length=2000,
        description="Bounded sanitized excerpt retained directly from this source",
    )
    published_at: str | None = Field(
        default=None,
        max_length=100,
        description="Provider-supplied publication date or timestamp when available",
    )
    source_type: str | None = Field(
        default=None,
        max_length=64,
        description="Provider-supplied or extraction-level source type when available",
    )


class SubReport(BaseModel):
    sub_question_id: str = Field(description="ID of the sub-question this report answers")
    question: str = Field(description="The original sub-question text")
    findings: str = Field(description="300-500 word summary of findings")
    key_facts: list[str] = Field(
        description="3-5 key facts discovered",
    )
    confidence: float = Field(
        description="Confidence score 0-1, where 1 is fully supported by evidence",
    )
    gaps: str = Field(description="Description of information gaps or limitations")
    sources: list[SourceRef] = Field(default_factory=list, description="Sources used")


class ReportSection(BaseModel):
    heading: str = Field(description="Section heading")
    content: str = Field(description="Section content in markdown")


class ResearchReport(BaseModel):
    title: str = Field(description="Research report title")
    executive_summary: str = Field(description="2-3 paragraph executive summary")
    sections: list[ReportSection] = Field(description="Report body sections")
    key_findings: list[str] = Field(description="5-10 key findings as bullet points")
    limitations: str = Field(description="Limitations and caveats of this research")
    sources: list[SourceRef] = Field(default_factory=list, description="Deduplicated source list")


class _StrictEvaluationModel(BaseModel):
    """Strict base contract for evaluator input and persisted output."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class PreSynthesisScores(_StrictEvaluationModel):
    intent_alignment: int = Field(ge=0, le=100)
    must_answer_coverage: int = Field(ge=0, le=100)
    source_relevance: int = Field(ge=0, le=100)
    source_quality: int = Field(ge=0, le=100)
    source_diversity: int = Field(ge=0, le=100)
    source_recency: int = Field(ge=0, le=100)
    grounding_consistency: int = Field(ge=0, le=100)
    contradiction_handling: int = Field(ge=0, le=100)
    synthesis_readiness: int = Field(ge=0, le=100)


class EvidenceIssue(_StrictEvaluationModel):
    id: str = Field(min_length=1, max_length=128)
    category: Literal[
        "intent_mismatch",
        "plan_structure",
        "coverage_gap",
        "source_relevance",
        "source_quality",
        "source_diversity",
        "source_recency",
        "grounding_gap",
        "contradiction",
        "synthesis_blocker",
    ]
    severity: Literal["minor", "major", "blocker"]
    description: str = Field(min_length=1)
    affected_sub_question_ids: list[str]
    source_urls: list[str]

    @model_validator(mode="after")
    def validate_references_are_unique(self):
        if len(self.affected_sub_question_ids) != len(set(self.affected_sub_question_ids)):
            raise ValueError("affected_sub_question_ids must be unique")
        if len(self.source_urls) != len(set(self.source_urls)):
            raise ValueError("source_urls must be unique")
        return self


class EvidenceRepairDirective(_StrictEvaluationModel):
    id: str = Field(min_length=1, max_length=128)
    issue_ids: list[str] = Field(min_length=1)
    target_sub_question_ids: list[str]
    objective: str = Field(min_length=1)
    suggested_queries: list[str]
    acceptance_criteria: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references_are_unique(self):
        if len(self.issue_ids) != len(set(self.issue_ids)):
            raise ValueError("issue_ids must be unique")
        if len(self.target_sub_question_ids) != len(set(self.target_sub_question_ids)):
            raise ValueError("target_sub_question_ids must be unique")
        return self


class PreSynthesisEvaluation(_StrictEvaluationModel):
    schema_version: Literal["pre-synthesis-evaluation.v1"]
    rubric_version: Literal["pre-synthesis-rubric.v1"]
    assessed_sub_question_ids: list[str] = Field(min_length=1)
    scores: PreSynthesisScores
    issues: list[EvidenceIssue]
    repair_directives: list[EvidenceRepairDirective]
    unresolved_questions: list[str]
    evaluation_limitations: list[str]
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_internal_references(self):
        if len(self.assessed_sub_question_ids) != len(set(self.assessed_sub_question_ids)):
            raise ValueError("assessed_sub_question_ids must be unique")

        issue_ids = [issue.id for issue in self.issues]
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("issue IDs must be unique")

        directive_ids = [directive.id for directive in self.repair_directives]
        if len(directive_ids) != len(set(directive_ids)):
            raise ValueError("repair directive IDs must be unique")

        known_issue_ids = set(issue_ids)
        covered_issue_ids: set[str] = set()
        for directive in self.repair_directives:
            unknown_issue_ids = set(directive.issue_ids) - known_issue_ids
            if unknown_issue_ids:
                raise ValueError(
                    "repair directives reference unknown issue IDs: "
                    + ", ".join(sorted(unknown_issue_ids))
                )
            covered_issue_ids.update(directive.issue_ids)

        uncovered_material_issues = {
            issue.id
            for issue in self.issues
            if issue.severity in {"major", "blocker"}
        } - covered_issue_ids
        if uncovered_material_issues:
            raise ValueError(
                "major and blocker issues require repair directives: "
                + ", ".join(sorted(uncovered_material_issues))
            )

        return self


class PreSynthesisEvaluationRun(_StrictEvaluationModel):
    status: Literal["completed", "failed"]
    evaluation: PreSynthesisEvaluation | None
    error_code: Literal[
        "missing_inputs",
        "timeout",
        "provider_error",
        "invalid_output",
        "invalid_references",
    ] | None
    evaluator_model: str = Field(min_length=1, max_length=200)
    attempts: int = Field(ge=0, le=2)
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_status_payload(self):
        if self.status == "completed":
            if self.evaluation is None or self.error_code is not None:
                raise ValueError("completed evaluation runs require evaluation and no error_code")
            if self.attempts < 1:
                raise ValueError("completed evaluation runs require at least one model attempt")
        elif self.evaluation is not None or self.error_code is None:
            raise ValueError("failed evaluation runs require error_code and no evaluation")
        return self


ClaimSupport = Literal[
    "supported",
    "partially_supported",
    "unsupported",
    "contradicted",
    "unverifiable",
]
CitationStatus = Literal[
    "correct",
    "missing",
    "incorrect",
    "ambiguous",
    "not_required",
]
PostRepairStage = Literal["synthesis", "evidence", "plan"]


class ReportSegment(_StrictEvaluationModel):
    """One deterministic, addressable surface of a candidate report."""

    id: str = Field(min_length=1, max_length=128)
    component: Literal[
        "title",
        "executive_summary",
        "section",
        "key_finding",
        "limitations",
    ]
    section_index: int | None = Field(default=None, ge=0)
    item_index: int | None = Field(default=None, ge=0)
    heading: str | None = None
    text: str = Field(min_length=1)


class EvidenceSource(_StrictEvaluationModel):
    """Stable source identity exposed to synthesis and evaluation prompts."""

    source_id: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1)
    title: str
    published_at: str | None = Field(default=None, max_length=100)
    source_type: str | None = Field(default=None, max_length=64)


class EvidenceUnit(_StrictEvaluationModel):
    """Evidence with an explicit direct-source or derived-summary boundary."""

    evidence_id: str = Field(min_length=1, max_length=64)
    sub_question_id: str = Field(min_length=1, max_length=256)
    provenance: Literal["source_excerpt", "derived_summary"]
    kind: Literal["source_excerpt", "finding", "key_fact"]
    text: str = Field(min_length=1)
    source_ids: list[str]

    @model_validator(mode="after")
    def validate_provenance_contract(self):
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("source_ids must be unique")
        if self.provenance == "source_excerpt":
            if self.kind != "source_excerpt" or len(self.source_ids) != 1:
                raise ValueError(
                    "source_excerpt evidence must link to exactly one source"
                )
        elif self.kind not in {"finding", "key_fact"} or self.source_ids:
            raise ValueError(
                "derived_summary evidence cannot claim direct source attribution"
            )
        return self


class ClaimEvidenceReference(_StrictEvaluationModel):
    evidence_id: str = Field(min_length=1, max_length=64)
    supporting_excerpt: str = Field(min_length=1)


class ClaimCitationAudit(_StrictEvaluationModel):
    status: CitationStatus
    cited_source_ids: list[str]
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source_ids_are_unique(self):
        if len(self.cited_source_ids) != len(set(self.cited_source_ids)):
            raise ValueError("cited_source_ids must be unique")
        return self


class AtomicClaimAudit(_StrictEvaluationModel):
    claim_id: str = Field(min_length=1, max_length=128)
    claim_text: str = Field(min_length=1)
    materiality: Literal["critical", "major", "minor"]
    support: ClaimSupport
    evidence_refs: list[ClaimEvidenceReference]
    citation: ClaimCitationAudit
    calibration: Literal["accurate", "overstated", "understated"]
    rationale: str = Field(min_length=1)


class ReportSegmentAudit(_StrictEvaluationModel):
    segment_id: str = Field(min_length=1, max_length=128)
    contains_material_claims: bool
    claims: list[AtomicClaimAudit]

    @model_validator(mode="after")
    def validate_claim_ids_are_unique(self):
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim IDs within a segment audit must be unique")
        if not self.contains_material_claims and self.claims:
            raise ValueError("claims require contains_material_claims=true")
        return self


class PostSynthesisScores(_StrictEvaluationModel):
    intent_alignment: int = Field(ge=0, le=100)
    material_claim_grounding: int = Field(ge=0, le=100)
    citation_fidelity: int = Field(ge=0, le=100)
    citation_completeness: int = Field(ge=0, le=100)
    contradiction_handling: int = Field(ge=0, le=100)
    coverage: int = Field(ge=0, le=100)
    coherence: int = Field(ge=0, le=100)
    limitations_calibration: int = Field(ge=0, le=100)


class ReportEvaluationIssue(_StrictEvaluationModel):
    id: str = Field(min_length=1, max_length=128)
    category: Literal[
        "unsupported_claim",
        "contradicted_claim",
        "missing_citation",
        "incorrect_citation",
        "overstatement",
        "coverage_gap",
        "contract_violation",
        "synthesis_failure",
        "incoherence",
        "weak_limitations",
    ]
    severity: Literal["minor", "major", "blocker"]
    claim_ids: list[str]
    segment_ids: list[str]
    affected_sub_question_ids: list[str]
    suggested_repair_stage: PostRepairStage
    suggested_queries: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references_are_unique(self):
        for field_name in (
            "claim_ids",
            "segment_ids",
            "affected_sub_question_ids",
            "suggested_queries",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        return self


class PostSynthesisEvaluation(_StrictEvaluationModel):
    schema_version: Literal["post-synthesis-eval.v1"]
    rubric_version: Literal["report-quality.v1"]
    segment_audits: list[ReportSegmentAudit] = Field(min_length=1)
    scores: PostSynthesisScores
    issues: list[ReportEvaluationIssue]
    unresolved_questions: list[str]
    summary: str = Field(min_length=1)


class PostSynthesisEvaluationRun(_StrictEvaluationModel):
    status: Literal["completed", "failed"]
    evaluation: PostSynthesisEvaluation | None
    error_code: Literal[
        "missing_inputs",
        "timeout",
        "provider_error",
        "invalid_output",
        "invalid_references",
        "section_generation_failure",
        "budget_exhausted",
    ] | None
    report_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    report_version: int | None = Field(default=None, ge=1)
    evaluator_model: str = Field(min_length=1, max_length=200)
    attempts: int = Field(ge=0, le=2)
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_status_payload(self):
        if (self.report_digest is None) != (self.report_version is None):
            raise ValueError("report_digest and report_version must be supplied together")
        if self.status == "completed":
            if self.evaluation is None or self.error_code is not None:
                raise ValueError("completed evaluation runs require evaluation and no error_code")
            if self.attempts < 1:
                raise ValueError("completed evaluation runs require at least one model attempt")
            if self.report_digest is None or self.report_version is None:
                raise ValueError("completed evaluation runs require a bound report subject")
        elif self.evaluation is not None or self.error_code is None:
            raise ValueError("failed evaluation runs require error_code and no evaluation")
        return self


PreSynthesisRoute = Literal[
    "accept",
    "targeted_repair",
    "partial_replan",
    "full_replan",
    "stop_incomplete",
]
PostSynthesisRoute = PreSynthesisRoute


class RepairStage(str, Enum):
    INITIAL = "initial"
    TARGETED_REPAIR = "targeted_repair"
    SYNTHESIS = "synthesis"
    EVIDENCE = "evidence"
    PARTIAL_REPLAN = "partial_replan"
    FULL_REPLAN = "full_replan"


class BudgetSnapshot(BaseModel):
    """Authoritative counters used by the deterministic recovery controller."""

    model_config = ConfigDict(extra="forbid")

    pre_evaluations_used: int = Field(default=0, ge=0)
    targeted_repairs_used: int = Field(default=0, ge=0)
    partial_replans_used: int = Field(default=0, ge=0)
    full_replans_used: int = Field(default=0, ge=0)
    total_recoveries_used: int = Field(default=0, ge=0)
    post_evaluations_used: int = Field(default=0, ge=0)
    synthesis_repairs_used: int = Field(default=0, ge=0)
    pre_evaluation_limit: int = Field(default=5, ge=1)
    targeted_repair_limit: int = Field(default=2, ge=0)
    partial_replan_limit: int = Field(default=1, ge=0)
    full_replan_limit: int = Field(default=1, ge=0)
    total_recovery_limit: int = Field(default=4, ge=0)
    post_evaluation_limit: int = Field(default=4, ge=1)
    synthesis_repair_limit: int = Field(default=2, ge=0)


class RoutingDecision(BaseModel):
    """One deterministic controller decision; never supplied by the evaluator LLM."""

    model_config = ConfigDict(extra="forbid")

    route: PreSynthesisRoute
    repair_stage: RepairStage
    reason_code: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1)
    affected_sub_question_ids: list[str]
    issue_ids: list[str]
    major_issue_ids: list[str]
    weighted_overall_score: float = Field(ge=0, le=100)
    affected_priority_ratio: float = Field(ge=0, le=1)
    score_gain: float | None = None
    closed_major_issue_ids: list[str]
    fingerprint: str = Field(min_length=1, max_length=64)
    escalated_from: PreSynthesisRoute | None = None
    budget: BudgetSnapshot
    evaluation_phase: Literal["pre_synthesis"] = "pre_synthesis"


class PostSynthesisRoutingDecision(RoutingDecision):
    """Typed five-level decision for the report-quality controller."""

    evaluation_phase: Literal["post_synthesis"] = "post_synthesis"
    target_report_segment_ids: list[str] = Field(default_factory=list)
    report_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    report_version: int | None = Field(default=None, ge=1)
    evaluation_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_report_subject(self):
        if (self.report_digest is None) != (self.report_version is None):
            raise ValueError("report_digest and report_version must be supplied together")
        if self.route == "accept" and (
            self.report_digest is None
            or self.report_version is None
            or self.evaluation_digest is None
        ):
            raise ValueError(
                "accept decisions require bound report and evaluation subjects"
            )
        return self


class ReportSegmentRevision(_StrictEvaluationModel):
    segment_id: str = Field(min_length=1, max_length=128)
    revised_text: str = Field(min_length=1)


class ReportRevisionPatch(_StrictEvaluationModel):
    schema_version: Literal["report-revision.v1"]
    resolved_issue_ids: list[str] = Field(min_length=1)
    updates: list[ReportSegmentRevision] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_references(self):
        if len(self.resolved_issue_ids) != len(set(self.resolved_issue_ids)):
            raise ValueError("resolved_issue_ids must be unique")
        segment_ids = [update.segment_id for update in self.updates]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("revision segment IDs must be unique")
        return self
