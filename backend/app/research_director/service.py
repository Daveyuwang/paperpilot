from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.client import LLMClient
from app.research_director.models import (
    AlternativeMethod,
    DatasetSpec,
    EvidenceClaim,
    EvidenceItem,
    EvidenceRelation,
    EvidenceStatus,
    ExperimentPlan,
    GeneratePlanRequest,
    GenerationMode,
    HandoffSpecification,
    HypothesisStatus,
    ImplementationPlan,
    MethodSpec,
    MetricSpec,
    Milestone,
    PlanLifecycleStatus,
    ResearchContract,
    ResearchGap,
    ResearchHypothesis,
    ResearchPlanBundle,
    ReviewerPerspective,
    ReviewIssue,
    ReviewIssueStatus,
    ReviewPlanRequest,
    ReviewReport,
    ReviewSeverity,
    ReviewState,
    ReviewVerdict,
    RevisePlanRequest,
    RevisionRecord,
    WorkPackage,
    review_report_digest,
    scientific_plan_digest,
)
from app.research_director.prompts import (
    INDEPENDENT_REVIEWER_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    REVISER_SYSTEM_PROMPT,
    build_generate_user_prompt,
    build_review_user_prompt,
    build_revise_user_prompt,
    build_validation_repair_prompt,
)

ModelT = TypeVar("ModelT", bound=BaseModel)
RequestLike = BaseModel | Mapping[str, Any]


_OBSERVED_PREDICATE = (
    r"achieved|yielded|outperformed|passed|succeeded|completed|finished|"
    r"implemented|wrote|written|built|tested|ran|run|executed|benchmarked|"
    r"deployed|published|verified|reproduced|showed|demonstrated|proved|confirmed|"
    r"complete|successful"
)
_CURRENT_WORK_SUBJECT = (
    r"paperpilot|research director|the agent|this agent|we|our (?:system|team|"
    r"project|study|research|model|method|implementation)|this (?:system|project|"
    r"study|research|experiment|implementation|model|method)|the proposed (?:model|"
    r"method|system|implementation)|the (?:model|method|system|implementation|code|"
    r"build|tests?|benchmarks?|experiments?|evaluations?|deployments?|results?|paper|"
    r"manuscript)|(?:tests?|benchmarks?|experiments?|evaluations?|results?)|"
    r"(?:the )?external (?:team|executor)"
)
_CURRENT_WORK_CLAIM_PATTERN = re.compile(
    rf"\b(?:{_CURRENT_WORK_SUBJECT})\b"
    r"(?P<prefix>(?:\s+(?:has|have|had|is|are|was|were|did|already|now|fully|"
    r"partially|successfully|significantly|empirically|externally|independently|"
    r"will|shall|would|should|must|may|might|could|can|not|never|planned|expected|"
    r"needs?|requires?|to|be)){0,7})\s+"
    rf"(?P<predicate>{_OBSERVED_PREDICATE})\b",
    re.IGNORECASE,
)
_BARE_OBSERVED_CLAIM_PATTERN = re.compile(
    r"^\s*(?:then\s+)?(?:achieved|yielded|outperformed|passed|succeeded|completed|"
    r"finished|implemented|tested|ran|executed|deployed|verified|reproduced|showed|"
    r"demonstrated|proved|confirmed)\b",
    re.IGNORECASE,
)
_FUTURE_OR_NEGATED_TAIL = re.compile(
    r"\b(?:will|shall|would|should|must|may|might|could|can|not|never|without|"
    r"to be|planned to|expected to|needs? to|requires?)\b",
    re.IGNORECASE,
)
_ATTRIBUTED_PRIOR_WORK_PREFIX = re.compile(
    r"^\s*(?:according to|prior\b|previous\b|reported\b|cited\b|"
    r"published (?:work|research|evidence|studies?)\b|"
    r"the (?:source|literature|authors?|cited study)\b)",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|[\r\n]+")
_CLAUSE_SPLIT = re.compile(r"\s*(?:,|\bbut\b|\bhowever\b|\byet\b)\s*", re.IGNORECASE)


def _contains_misleading_observed_work(text: str) -> bool:
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        if not sentence:
            continue
        if _ATTRIBUTED_PRIOR_WORK_PREFIX.search(sentence):
            continue
        for clause in _CLAUSE_SPLIT.split(sentence):
            for observed in _CURRENT_WORK_CLAIM_PATTERN.finditer(clause):
                prefix = observed.group("prefix") or ""
                # A modal/negation applies only in the final coordinated segment
                # before the predicate, so "we may inspect logs and achieved" is
                # not mistaken for future work.
                tail = re.split(r"\b(?:and|or|then)\b", prefix, flags=re.IGNORECASE)[-1]
                if _FUTURE_OR_NEGATED_TAIL.search(tail):
                    continue
                return True
            if _BARE_OBSERVED_CLAIM_PATTERN.search(clause):
                return True
    return False


def _iter_plan_generated_strings(value: Any, path: str = "plan"):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_plan_generated_strings(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_plan_generated_strings(item, f"{path}.{key}")


def _ensure_pre_execution_language(plan: ResearchPlanBundle) -> None:
    """Reject overt claims that PaperPilot already performed planned work.

    Caller-frozen contracts and evidence may quote prior work, so they are not
    scanned. Independent review handles subtler scientific overclaims; this
    deterministic gate blocks explicit execution/publication assertions before
    a generated or revised plan can be persisted.
    """

    generated_sections: dict[str, Any] = {
        "gaps": plan.gaps,
        "hypotheses": plan.hypotheses,
        "methods": plan.methods,
        "experiments": plan.experiments,
        "implementation_plan": plan.implementation_plan,
        "limitations": plan.limitations,
        "unresolved_questions": plan.unresolved_questions,
        "generation_warnings": plan.generation_warnings,
        "revision_record": plan.revision_record,
    }
    rendered = {
        key: (
            value.model_dump(mode="json")
            if isinstance(value, BaseModel)
            else [
                item.model_dump(mode="json") if isinstance(item, BaseModel) else item
                for item in value
            ]
            if isinstance(value, list)
            else value
        )
        for key, value in generated_sections.items()
    }
    for path, text in _iter_plan_generated_strings(rendered):
        if _contains_misleading_observed_work(text):
            raise ValueError(
                "plan contains a misleading executed/verified/published claim at "
                f"{path}; describe only future external work"
            )


async def generate_plan(
    llm: LLMClient,
    request: GeneratePlanRequest | Mapping[str, Any],
    *,
    trace: Any = None,
) -> ResearchPlanBundle:
    """Generate a complete pre-execution research package.

    The function has no persistence or execution side effects. If the model call or
    schema validation fails, it returns a deterministic, explicitly limited draft.
    """

    req = _coerce_request(request, GeneratePlanRequest)
    plan_id = _stable_id("rd-plan", req.model_dump(mode="json", exclude_none=True))

    def prepare(raw: Any) -> Any:
        payload = _unwrap_object(raw, "plan", "research_plan", "result")
        if not isinstance(payload, dict):
            return payload
        prepared = dict(payload)
        prepared.update(
            {
                "schema_version": "1.0",
                "plan_id": plan_id,
                "version": 1,
                "supersedes_plan_id": None,
                "lifecycle_status": PlanLifecycleStatus.DRAFT.value,
                "generation_mode": GenerationMode.MODEL.value,
                "evidence_catalog": [
                    item.model_dump(mode="json") for item in req.evidence
                ],
                "revision_record": None,
            }
        )
        raw_warnings = prepared.get("generation_warnings")
        if isinstance(raw_warnings, list):
            prepared["generation_warnings"] = _merge_strings(
                raw_warnings, req.evidence_warnings
            )
        elif req.evidence_warnings:
            prepared["generation_warnings"] = list(req.evidence_warnings)
        if req.contract is not None:
            raw_contract = prepared.get("contract")
            merged_contract = dict(raw_contract) if isinstance(raw_contract, dict) else {}
            merged_contract.update(
                req.contract.model_dump(mode="json", exclude_none=True)
            )
            prepared["contract"] = merged_contract
        return prepared

    try:
        return await _validated_create_json(
            llm=llm,
            system=PLANNER_SYSTEM_PROMPT,
            user_prompt=build_generate_user_prompt(req),
            output_model=ResearchPlanBundle,
            task="research-plan generation",
            prepare=prepare,
            post_validate=_ensure_pre_execution_language,
            max_tokens=7_500,
            temperature=0.2,
            trace=trace,
        )
    except Exception:
        return _fallback_generate(req, plan_id=plan_id)


async def review_plan(
    llm: LLMClient,
    request: ReviewPlanRequest | Mapping[str, Any],
    *,
    trace: Any = None,
) -> ReviewReport:
    """Independently review a plan without revising or executing it."""

    req = _coerce_request(request, ReviewPlanRequest)
    plan_digest = scientific_plan_digest(req.plan)
    review_id = _stable_id(
        "rd-review",
        {
            "plan_id": req.plan.plan_id,
            "plan_version": req.plan.version,
            "plan_digest": plan_digest,
            "perspectives": [item.value for item in req.perspectives],
            "instructions": req.review_instructions,
            "evidence_ids": [item.id for item in req.evidence],
        },
    )

    def prepare(raw: Any) -> Any:
        payload = _unwrap_object(raw, "review", "review_report", "result")
        if not isinstance(payload, dict):
            return payload
        prepared = dict(payload)
        prepared.update(
            {
                "schema_version": "1.0",
                "review_id": review_id,
                "reviewed_plan_id": req.plan.plan_id,
                "reviewed_plan_version": req.plan.version,
                "reviewed_plan_digest": plan_digest,
                "review_state": ReviewState.INDEPENDENT_REVIEW_COMPLETE.value,
            }
        )
        raw_issues = prepared.get("issues")
        if isinstance(raw_issues, list):
            prepared["issues"] = [
                {**item, "status": ReviewIssueStatus.OPEN.value}
                if isinstance(item, dict)
                else item
                for item in raw_issues
            ]
        return prepared

    def validate_coverage(report: ReviewReport) -> None:
        completed = set(report.perspectives_completed)
        missing = set(req.perspectives) - completed
        if missing:
            values = sorted(item.value for item in missing)
            raise ValueError(f"independent review omitted perspectives: {values}")
        carried_unresolved = set(
            req.plan.revision_record.unresolved_issue_ids
            if req.plan.revision_record is not None
            else []
        )
        issues_by_id = {item.id: item for item in report.issues}
        missing_carried = carried_unresolved - set(issues_by_id)
        if missing_carried:
            raise ValueError(
                "independent review omitted carried unresolved issues: "
                f"{sorted(missing_carried)}"
            )
        closed_carried = sorted(
            issue_id
            for issue_id in carried_unresolved
            if issues_by_id[issue_id].status != ReviewIssueStatus.OPEN
        )
        if closed_carried:
            raise ValueError(
                "carried unresolved issues must remain open in the next review: "
                f"{closed_carried}"
            )

    try:
        return await _validated_create_json(
            llm=llm,
            system=INDEPENDENT_REVIEWER_SYSTEM_PROMPT,
            user_prompt=build_review_user_prompt(req),
            output_model=ReviewReport,
            task="independent plan review",
            prepare=prepare,
            post_validate=validate_coverage,
            max_tokens=4_500,
            temperature=0.0,
            trace=trace,
        )
    except Exception:
        return _fallback_review(req, review_id=review_id)


async def revise_plan(
    llm: LLMClient,
    request: RevisePlanRequest | Mapping[str, Any],
    *,
    trace: Any = None,
) -> ResearchPlanBundle:
    """Create a new, review-required plan version; never execute the plan."""

    req = _coerce_request(request, RevisePlanRequest)
    source_plan_digest = scientific_plan_digest(req.plan)
    source_review_digest = review_report_digest(req.review)
    merged_evidence = _merge_evidence(req.plan.evidence_catalog, req.evidence)
    plan_id = _stable_id(
        "rd-plan",
        {
            "supersedes": req.plan.plan_id,
            "version": req.plan.version + 1,
            "review_id": req.review.review_id,
            "instructions": req.revision_instructions,
            "evidence_ids": [item.id for item in merged_evidence],
        },
    )

    def prepare(raw: Any) -> Any:
        payload = _unwrap_object(raw, "plan", "revised_plan", "result")
        if not isinstance(payload, dict):
            return payload
        prepared = dict(payload)
        prepared.update(
            {
                "schema_version": "1.0",
                "plan_id": plan_id,
                "version": req.plan.version + 1,
                "supersedes_plan_id": req.plan.plan_id,
                "lifecycle_status": PlanLifecycleStatus.REVIEW_REQUIRED.value,
                "generation_mode": GenerationMode.MODEL.value,
                "evidence_catalog": [
                    item.model_dump(mode="json") for item in merged_evidence
                ],
            }
        )
        revision_record = prepared.get("revision_record")
        if isinstance(revision_record, dict):
            prepared["revision_record"] = {
                **revision_record,
                "source_plan_digest": source_plan_digest,
                "source_review_digest": source_review_digest,
            }
        raw_warnings = prepared.get("generation_warnings")
        if isinstance(raw_warnings, list):
            prepared["generation_warnings"] = _merge_strings(
                raw_warnings,
                req.plan.generation_warnings,
                req.evidence_warnings,
            )
        else:
            prepared["generation_warnings"] = _merge_strings(
                req.plan.generation_warnings,
                req.evidence_warnings,
            )
        return prepared

    def validate_revision(plan: ResearchPlanBundle) -> None:
        _ensure_pre_execution_language(plan)
        record = plan.revision_record
        if record is None:
            raise ValueError("revised plan requires revision_record")
        if record.review_id != req.review.review_id:
            raise ValueError("revision_record.review_id must match the supplied review")
        if record.source_plan_digest != source_plan_digest:
            raise ValueError("revision_record.source_plan_digest is stale")
        if record.source_review_digest != source_review_digest:
            raise ValueError("revision_record.source_review_digest is stale")
        known_issue_ids = {item.id for item in req.review.issues}
        if req.plan.revision_record is not None:
            known_issue_ids.update(
                req.plan.revision_record.unresolved_issue_ids
            )
        classified = set(record.addressed_issue_ids) | set(record.unresolved_issue_ids)
        missing = known_issue_ids - classified
        unknown = classified - known_issue_ids
        if missing:
            raise ValueError(f"revision_record omitted review issues: {sorted(missing)}")
        if unknown:
            raise ValueError(f"revision_record contains unknown issue IDs: {sorted(unknown)}")

    try:
        return await _validated_create_json(
            llm=llm,
            system=REVISER_SYSTEM_PROMPT,
            user_prompt=build_revise_user_prompt(req),
            output_model=ResearchPlanBundle,
            task="research-plan revision",
            prepare=prepare,
            post_validate=validate_revision,
            max_tokens=7_500,
            temperature=0.1,
            trace=trace,
        )
    except Exception:
        return _fallback_revise(req, plan_id=plan_id, evidence=merged_evidence)


async def _validated_create_json(
    *,
    llm: LLMClient,
    system: str,
    user_prompt: str,
    output_model: type[ModelT],
    task: str,
    prepare: Callable[[Any], Any],
    max_tokens: int,
    temperature: float,
    trace: Any,
    post_validate: Callable[[ModelT], None] | None = None,
) -> ModelT:
    raw = await llm.create_json(
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
        max_retries=2,
        trace=trace,
    )
    try:
        return _validate_output(
            raw,
            output_model=output_model,
            prepare=prepare,
            post_validate=post_validate,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        errors = _validation_errors(exc)

    repair_prompt = build_validation_repair_prompt(
        task=task,
        invalid_payload=raw,
        validation_errors=errors,
        output_model=output_model,
    )
    repaired = await llm.create_json(
        system=system,
        messages=[
            {"role": "user", "content": user_prompt},
            {"role": "user", "content": repair_prompt},
        ],
        max_tokens=max_tokens,
        temperature=0.0,
        max_retries=1,
        trace=trace,
    )
    return _validate_output(
        repaired,
        output_model=output_model,
        prepare=prepare,
        post_validate=post_validate,
    )


def _validate_output(
    raw: Any,
    *,
    output_model: type[ModelT],
    prepare: Callable[[Any], Any],
    post_validate: Callable[[ModelT], None] | None,
) -> ModelT:
    value = output_model.model_validate(prepare(raw))
    if post_validate is not None:
        post_validate(value)
    return value


def _coerce_request(value: RequestLike, model: type[ModelT]) -> ModelT:
    if isinstance(value, model):
        return value
    return model.model_validate(value)


def _unwrap_object(raw: Any, *keys: str) -> Any:
    if not isinstance(raw, dict):
        return raw
    for key in keys:
        nested = raw.get(key)
        if isinstance(nested, dict):
            return nested
    return raw


def _validation_errors(exc: Exception) -> list[dict[str, Any]]:
    if isinstance(exc, ValidationError):
        return exc.errors(include_url=False, include_input=False)
    return [{"type": type(exc).__name__, "message": str(exc)}]


def _stable_id(prefix: str, value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _merge_evidence(
    original: list[EvidenceItem], additional: list[EvidenceItem]
) -> list[EvidenceItem]:
    merged = list(original)
    known: dict[str, EvidenceItem] = {item.id: item for item in original}
    for item in additional:
        existing = known.get(item.id)
        if existing is not None:
            if existing.model_dump(mode="json") != item.model_dump(mode="json"):
                raise ValueError(
                    f"evidence ID {item.id!r} cannot overwrite existing provenance"
                )
            continue
        known[item.id] = item
        merged.append(item)
    return merged


def _merge_strings(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item not in seen:
                merged.append(item)
                seen.add(item)
    return merged


def _fallback_generate(
    request: GeneratePlanRequest,
    *,
    plan_id: str,
) -> ResearchPlanBundle:
    brief = request.research_brief.strip()
    title = _title_from_brief(brief)
    desired = request.desired_deliverables or [
        "Evidence and gap map",
        "Hypothesis portfolio",
        "Method and experiment design",
        "Implementation-ready handoff package",
    ]
    fallback_contract = ResearchContract(
        title=title,
        research_question=brief,
        objective=f"Develop an evidence-backed implementation plan for: {brief}",
        scope_inclusions=[
            "Literature and evidence assessment",
            "Research-gap and hypothesis formulation",
            "Method, experiment, and implementation planning",
        ],
        scope_exclusions=[
            "Writing or modifying production code",
            "Running builds, tests, experiments, or benchmarks",
            "Claiming empirical verification or publishing results",
        ],
        constraints=request.constraints,
        assumptions=["External specialists will execute and verify the approved plan"],
        unknowns=["Evidence coverage and implementation constraints require confirmation"],
        success_criteria=[
            "Every proposed task has explicit inputs, outputs, dependencies, and acceptance criteria",
            "All material claims are linked to supplied evidence or marked unsupported",
            "An independent reviewer closes all blocker issues before handoff",
        ],
        failure_criteria=[
            "The plan implies that unexecuted work or unobserved results were completed",
            "Material recommendations depend on uncited or fabricated evidence",
        ],
        allowed_sources=["Caller-provided evidence and sources approved by the human researcher"],
        excluded_sources=[],
        required_deliverables=desired,
        human_decisions_required=[
            "Confirm the research scope and evidence corpus",
            "Approve the final plan before external execution",
        ],
    )
    if request.contract is not None:
        contract_payload = fallback_contract.model_dump(mode="json")
        contract_payload.update(
            request.contract.model_dump(mode="json", exclude_none=True)
        )
        contract = ResearchContract.model_validate(contract_payload)
    else:
        contract = fallback_contract

    claims = [
        EvidenceClaim(
            id=f"claim-{index:02d}",
            statement=(
                item.summary
                or f"Source {item.title!r} is available but has not been synthesized into a supported claim."
            ),
            evidence_item_ids=[item.id],
            relation=EvidenceRelation.UNKNOWN,
            status=EvidenceStatus.UNKNOWN,
            confidence=0.0,
            limitations=["The deterministic fallback did not assess source-to-claim entailment"],
        )
        for index, item in enumerate(request.evidence, start=1)
    ]
    claim_ids = [item.id for item in claims]

    gap = ResearchGap(
        id="gap-01",
        description=f"The decisive evidence and strongest viable approach for {brief} remain unresolved.",
        evidence_claim_ids=claim_ids,
        contrary_claim_ids=[],
        impact="The implementation direction cannot be selected confidently until this gap is resolved.",
        testability=(
            "Compare candidate approaches against explicit evidence coverage and a "
            "controlled external evaluation."
        ),
        novelty_assessment="Novelty is undetermined until a targeted prior-work search is independently reviewed.",
        novelty_confidence=0.0,
        uncertainties=["Relevant prior work may be missing", "Feasibility constraints need human confirmation"],
    )
    hypothesis = ResearchHypothesis(
        id="hypothesis-01",
        statement=(
            "A defensible implementation direction can be selected after the identified evidence "
            "gap is resolved through comparative analysis and controlled external evaluation."
        ),
        rationale=(
            "This conservative hypothesis avoids inventing a domain-specific result "
            "when structured model output is unavailable."
        ),
        evidence_claim_ids=claim_ids,
        status=HypothesisStatus.PROPOSED,
        falsifiable_predictions=[
            "At least one candidate method will satisfy the predeclared acceptance criteria under external evaluation"
        ],
        differentiation_from_prior_work="Undetermined pending a targeted, source-backed novelty assessment.",
        strongest_counterargument=(
            "Available evidence may be insufficient to distinguish candidates or "
            "support implementation."
        ),
        minimum_validation=[
            "Independent evidence review",
            "External comparison against a credible baseline using predeclared metrics",
        ],
        dependencies=["Approved evidence corpus", "Human-confirmed constraints"],
        risks=["Evidence gaps may invalidate the proposed direction"],
    )
    method = MethodSpec(
        id="method-01",
        title="Evidence-gated comparative method selection",
        summary=(
            "Resolve critical evidence gaps, compare candidate methods, and hand an "
            "approved specification to an external executor."
        ),
        hypothesis_ids=[hypothesis.id],
        components=["Evidence matrix", "Candidate method comparison", "External evaluation protocol"],
        procedure=[
            "Confirm the research contract and evidence corpus",
            "Map supporting, conflicting, and missing evidence",
            "Compare candidate methods against constraints and falsifiable predictions",
            "Freeze the experiment protocol before external execution",
        ],
        interfaces_or_boundaries=["PaperPilot produces plans and review artifacts only; execution is external"],
        assumptions=["The external executor can access the required data and compute"],
        alternatives_considered=[
            AlternativeMethod(
                title="Immediate implementation",
                description="Begin implementation before resolving evidence and evaluation gaps.",
                rejection_reason="It risks avoidable rework and unsupported research claims.",
                reconsider_when="Only after blockers are closed and a human approves handoff.",
            )
        ],
        selection_rationale=(
            "A gated comparison is the safest useful fallback when a domain-specific "
            "plan cannot be validated."
        ),
        risks=["The generic method requires domain-specific refinement"],
    )
    experiment = ExperimentPlan(
        id="experiment-01",
        title="External controlled comparison",
        research_question=brief,
        hypothesis_ids=[hypothesis.id],
        method_id=method.id,
        datasets=[
            DatasetSpec(
                name="Human-approved evaluation dataset",
                purpose="Evaluate candidate methods against the research objective",
                split_or_sampling="Define train/development/test separation before external execution",
                access_or_license_notes="Access and licensing require human confirmation",
                leakage_checks=["Check overlap across splits and prior-work contamination"],
            )
        ],
        baselines=["Current strongest applicable baseline, to be confirmed by evidence review"],
        metrics=[
            MetricSpec(
                name="Primary task metric",
                definition="A domain-appropriate primary metric selected before execution",
                direction="higher_is_better",
                success_threshold="Set from the confirmed baseline and practical significance target",
            )
        ],
        controls=["Hold dataset, evaluation protocol, and resource budget constant across candidates"],
        ablations=["Remove each proposed component to estimate its contribution"],
        negative_tests=["Test a deliberately weak or null intervention"],
        statistical_analysis=(
            "Predeclare uncertainty intervals and an appropriate paired significance "
            "or equivalence test."
        ),
        seeds_or_repetitions=(
            "Use enough independent repetitions to estimate variance; exact count "
            "requires a power analysis."
        ),
        stop_conditions=[
            "Stop if data leakage or invalid measurement is detected",
            "Stop when the predeclared resource budget is reached",
        ],
        expected_artifacts=["External run manifest", "Metric table", "Error analysis", "Limitations report"],
        acceptance_criteria=[
            "Protocol is frozen before execution",
            "Results include uncertainty and comparison to the confirmed baseline",
        ],
        risks=["Dataset, baseline, and metric details remain unresolved"],
    )
    implementation = _fallback_implementation(brief, method.id, experiment.id)
    return ResearchPlanBundle(
        plan_id=plan_id,
        lifecycle_status=PlanLifecycleStatus.DRAFT,
        generation_mode=GenerationMode.DETERMINISTIC_FALLBACK,
        contract=contract,
        evidence_catalog=request.evidence,
        evidence_claims=claims,
        gaps=[gap],
        hypotheses=[hypothesis],
        methods=[method],
        experiments=[experiment],
        implementation_plan=implementation,
        limitations=[
            "The model-authored plan could not be validated; this conservative draft "
            "requires independent human refinement."
        ],
        unresolved_questions=[
            "Which directly relevant prior work, datasets, baselines, and operational constraints must be added?"
        ],
        generation_warnings=_merge_strings(
            request.evidence_warnings,
            [
                "Deterministic fallback used; do not approve or hand off without independent review."
            ],
        ),
    )


def _fallback_implementation(
    brief: str,
    method_id: str,
    experiment_id: str,
) -> ImplementationPlan:
    packages = [
        WorkPackage(
            id="wp-01",
            title="Resolve evidence and scope blockers",
            objective=f"Freeze an evidence-backed research contract for {brief}.",
            tasks=[
                "Complete the literature and evidence matrix",
                "Resolve scope, novelty, data, baseline, and metric decisions",
            ],
            inputs=["Research brief", "Supplied evidence", "Human constraints"],
            outputs=["Approved research contract", "Reviewed evidence and gap map"],
            acceptance_criteria=["All blocker evidence gaps are closed or explicitly accepted by a human"],
            owner_role="Research lead",
            risks=["Missing prior work may change the recommended direction"],
        ),
        WorkPackage(
            id="wp-02",
            title="Freeze method and experiment specifications",
            objective=f"Turn {method_id} and {experiment_id} into externally executable specifications.",
            tasks=[
                "Finalize interfaces and component boundaries",
                "Freeze datasets, baselines, metrics, controls, statistics, and stop conditions",
            ],
            inputs=["Reviewed evidence and gap map", "Candidate hypotheses"],
            outputs=["Method specification", "Pre-registered external experiment protocol"],
            acceptance_criteria=["An independent reviewer reports no open blocker or major issue"],
            dependency_ids=["wp-01"],
            owner_role="Research and evaluation leads",
            risks=["Unresolved data access or statistical-power constraints"],
        ),
        WorkPackage(
            id="wp-03",
            title="External implementation and evaluation handoff",
            objective=(
                "Enable an external engineering or coding agent team to implement and "
                "evaluate the approved design."
            ),
            tasks=[
                "Implement according to the frozen interface and method specifications",
                "Execute the frozen experiment protocol outside PaperPilot",
                "Return manifests, results, failures, and deviations for review",
            ],
            inputs=["Approved method specification", "Frozen experiment protocol"],
            outputs=["Externally produced implementation and evaluation artifacts"],
            acceptance_criteria=[
                "External artifacts conform to the result contract",
                "Any protocol deviation is documented for later human review",
            ],
            dependency_ids=["wp-02"],
            owner_role="External engineering and evaluation team",
            risks=["External execution may expose feasibility issues requiring plan revision"],
        ),
    ]
    return ImplementationPlan(
        objective=f"Prepare an externally executable and independently reviewable implementation path for {brief}.",
        architecture_or_method_summary=(
            "Evidence-gated method selection followed by a frozen external evaluation "
            "protocol."
        ),
        work_packages=packages,
        milestones=[
            Milestone(
                name="Research package approved for handoff",
                work_package_ids=["wp-01", "wp-02"],
                exit_criteria=["No open blocker or major review issues", "Human approval recorded"],
            ),
            Milestone(
                name="External execution package prepared",
                work_package_ids=["wp-03"],
                exit_criteria=["Inputs, outputs, acceptance criteria, and result contract are complete"],
            ),
        ],
        unresolved_decisions=["Domain-specific implementation interfaces and resource budget"],
        resource_assumptions=["External execution resources are provided after human approval"],
        fallback_strategies=["Reduce scope to the minimum falsifiable experiment if constraints block the full plan"],
        handoff=HandoffSpecification(
            target_roles=["Engineering or coding agent team", "External evaluation owner"],
            prerequisites=["Independent review complete", "Human approval for external execution"],
            included_artifacts=[
                "Research contract",
                "Evidence and gap map",
                "Hypotheses",
                "Method specification",
                "Experiment protocol",
                "Implementation work packages",
            ],
            execution_instructions=[
                "Do not change the frozen protocol without recording a deviation",
                "Return failures and negative results as first-class artifacts",
            ],
            external_result_contract=[
                "Run and environment manifest",
                "Raw and summarized metrics",
                "Logs and failure records",
                "Protocol deviations",
                "Artifact locations and provenance",
            ],
        ),
    )


def _fallback_review(
    request: ReviewPlanRequest,
    *,
    review_id: str,
) -> ReviewReport:
    carried_unresolved = list(
        request.plan.revision_record.unresolved_issue_ids
        if request.plan.revision_record is not None
        else []
    )
    issues = [
        ReviewIssue(
            id=issue_id,
            perspective=ReviewerPerspective.RISK,
            severity=ReviewSeverity.MAJOR,
            artifact_path="revision_record.unresolved_issue_ids",
            problem=f"Prior review issue {issue_id!r} remains unresolved.",
            evidence=(
                "The current plan revision record explicitly carries this issue "
                "as unresolved."
            ),
            impact=(
                "Approval would erase unresolved review lineage without a recorded "
                "fix or accepted-risk decision."
            ),
            required_fix=(
                "Resolve this issue in a new revision and preserve its ID in the "
                "revision classification."
            ),
            status=ReviewIssueStatus.OPEN,
        )
        for issue_id in carried_unresolved
    ]
    unavailable_issue_id = "issue-independent-review-unavailable"
    while unavailable_issue_id in set(carried_unresolved):
        unavailable_issue_id += "-fallback"
    issues.append(ReviewIssue(
        id=unavailable_issue_id,
        perspective=ReviewerPerspective.RISK,
        severity=ReviewSeverity.BLOCKER,
        artifact_path="review",
        problem="The independent structured review could not be validated.",
        evidence="No schema-valid independent review artifact is available.",
        impact="The plan cannot be considered independently reviewed or safe for handoff.",
        required_fix="Run an independent model or human review across every requested perspective.",
        status=ReviewIssueStatus.OPEN,
    ))
    return ReviewReport(
        review_id=review_id,
        reviewed_plan_id=request.plan.plan_id,
        reviewed_plan_version=request.plan.version,
        reviewed_plan_digest=scientific_plan_digest(request.plan),
        review_state=ReviewState.FALLBACK_REQUIRES_HUMAN_REVIEW,
        verdict=ReviewVerdict.BLOCKED,
        perspectives_completed=[],
        issues=issues,
        strengths=[],
        summary="Independent review is unavailable; this fallback deliberately blocks approval and handoff.",
        required_next_step="Obtain a complete independent human or model review before revising or approving the plan.",
    )


def _fallback_revise(
    request: RevisePlanRequest,
    *,
    plan_id: str,
    evidence: list[EvidenceItem],
) -> ResearchPlanBundle:
    pending_issue_ids = {
        item.id for item in request.review.issues
    }
    if request.plan.revision_record is not None:
        pending_issue_ids.update(
            request.plan.revision_record.unresolved_issue_ids
        )
    warnings = _merge_strings(
        request.plan.generation_warnings,
        request.evidence_warnings,
    )
    warning = "Deterministic fallback used; no model-authored revision was applied."
    if warning not in warnings:
        warnings.append(warning)
    limitations = list(request.plan.limitations)
    limitation = "Review issues remain unresolved pending a valid independent revision."
    if limitation not in limitations:
        limitations.append(limitation)
    payload = request.plan.model_dump(mode="json")
    payload.update(
        {
            "plan_id": plan_id,
            "version": request.plan.version + 1,
            "supersedes_plan_id": request.plan.plan_id,
            "lifecycle_status": PlanLifecycleStatus.REVIEW_REQUIRED.value,
            "generation_mode": GenerationMode.DETERMINISTIC_FALLBACK.value,
            "evidence_catalog": [item.model_dump(mode="json") for item in evidence],
            "limitations": limitations,
            "generation_warnings": warnings,
            "revision_record": RevisionRecord(
                review_id=request.review.review_id,
                source_plan_digest=scientific_plan_digest(request.plan),
                source_review_digest=review_report_digest(request.review),
                addressed_issue_ids=[],
                unresolved_issue_ids=sorted(pending_issue_ids),
                changes=[
                    "Preserved the prior plan unchanged and recorded that independent revision requires human action."
                ],
            ).model_dump(mode="json"),
        }
    )
    return ResearchPlanBundle.model_validate(payload)


def _title_from_brief(brief: str) -> str:
    first_line = brief.splitlines()[0].strip()
    if len(first_line) <= 96:
        return first_line
    return first_line[:93].rstrip() + "..."
