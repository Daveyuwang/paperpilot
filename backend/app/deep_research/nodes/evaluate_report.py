from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Literal

import structlog
from langchain_core.exceptions import OutputParserException
from langgraph.runtime import Runtime
from pydantic import ValidationError

from app.deep_research.context import DeepResearchContext
from app.deep_research.llm_factory import make_structured_llm, resolved_model_name
from app.deep_research.models import (
    BudgetSnapshot,
    EvidenceSource,
    EvidenceUnit,
    PostSynthesisEvaluation,
    PostSynthesisEvaluationRun,
    ResearchReport,
    SubQuestion,
    SubReport,
)
from app.deep_research.prompts import (
    POST_SYNTHESIS_EVALUATE_SYSTEM,
    POST_SYNTHESIS_EVALUATE_USER,
)
from app.deep_research.provenance import (
    build_evidence_inventory,
    build_report_segments,
    normalize_source_url,
    report_digest,
)
from app.deep_research.state import DeepResearchState

logger = structlog.get_logger()

POST_EVALUATION_TIMEOUT_SECONDS = 90
MAX_POST_EVALUATION_ATTEMPTS = 2
DEFAULT_POST_EVALUATOR_MODEL = "deepseek-v4-pro"

_CITATION_MARKER_RE = re.compile(r"\[(E|S):([^\]]+)\]")
_CITATION_ID_RE = re.compile(r"(?:ev|src)-[0-9a-f]{16}")

PostEvaluationErrorCode = Literal[
    "missing_inputs",
    "timeout",
    "provider_error",
    "invalid_output",
    "invalid_references",
    "section_generation_failure",
    "budget_exhausted",
]


class _MissingPostEvaluationInputs(ValueError):
    pass


class _InvalidPostEvaluationReferences(ValueError):
    pass


class _SectionGenerationFailure(ValueError):
    pass


def _candidate_report(state: DeepResearchState) -> ResearchReport:
    raw_report = state.get("candidate_report")
    if isinstance(raw_report, ResearchReport):
        return raw_report
    if isinstance(raw_report, dict):
        try:
            return ResearchReport.model_validate(raw_report)
        except ValidationError as exc:
            raise _MissingPostEvaluationInputs("candidate report is invalid") from exc
    raise _MissingPostEvaluationInputs("candidate report is required")


def _report_version(state: DeepResearchState) -> int:
    version = state.get("report_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise _MissingPostEvaluationInputs("a positive report version is required")
    return version


def _contains_section_failure(report: ResearchReport) -> bool:
    failure_markers = (
        "section generation failed",
        "report outline generation failed",
        "synthesis section error",
    )
    if not report.sections:
        return True
    return any(
        not section.content.strip()
        or any(marker in section.content.casefold() for marker in failure_markers)
        for section in report.sections
    )


def _validate_inputs(
    state: DeepResearchState,
) -> tuple[
    ResearchReport,
    list[SubQuestion],
    list[SubReport],
    list[EvidenceSource],
    list[EvidenceUnit],
]:
    topic = state.get("topic", "")
    if not isinstance(topic, str) or not topic.strip():
        raise _MissingPostEvaluationInputs("topic is required")

    raw_questions = state.get("sub_questions", [])
    raw_reports = state.get("sub_reports", [])
    if not isinstance(raw_questions, list) or not isinstance(raw_reports, list):
        raise _MissingPostEvaluationInputs("active plan and retained evidence must be lists")
    try:
        questions = [SubQuestion.model_validate(item) for item in raw_questions]
        reports = [SubReport.model_validate(item) for item in raw_reports]
    except (TypeError, ValueError, ValidationError) as exc:
        raise _MissingPostEvaluationInputs(
            "active plan and retained evidence are invalid"
        ) from exc
    if not questions or not reports:
        raise _MissingPostEvaluationInputs("active plan and retained evidence are required")

    active_ids = [question.id.strip() for question in questions]
    report_ids = [report.sub_question_id.strip() for report in reports]
    if (
        any(not identifier for identifier in active_ids)
        or len(active_ids) != len(set(active_ids))
        or any(not identifier for identifier in report_ids)
        or len(report_ids) != len(set(report_ids))
        or set(report_ids) != set(active_ids)
    ):
        raise _MissingPostEvaluationInputs(
            "candidate evaluation requires one retained report per active sub-question"
        )

    report = _candidate_report(state)
    if _contains_section_failure(report):
        raise _SectionGenerationFailure("candidate contains a failed report section")
    try:
        segments = build_report_segments(report)
    except (TypeError, ValueError, ValidationError) as exc:
        raise _MissingPostEvaluationInputs("candidate report surfaces are invalid") from exc
    if not segments or any(not segment.text.strip() for segment in segments):
        raise _MissingPostEvaluationInputs("every candidate report surface must be non-empty")

    sources, evidence = build_evidence_inventory(reports)
    if not sources or not evidence:
        raise _MissingPostEvaluationInputs("approved source metadata and evidence are required")
    if not any(unit.provenance == "source_excerpt" for unit in evidence):
        raise _MissingPostEvaluationInputs("direct source excerpt evidence is required")

    known_source_urls = {source.url for source in sources}
    for source in report.sources:
        normalized_url = normalize_source_url(source.url)
        if not normalized_url or normalized_url not in known_source_urls:
            raise _InvalidPostEvaluationReferences(
                "candidate bibliography references a source outside the evidence dossier"
            )

    return report, questions, reports, sources, evidence


def _build_post_synthesis_audit_json(
    state: DeepResearchState,
    *,
    report: ResearchReport,
    questions: list[SubQuestion],
    sources: list[EvidenceSource],
    evidence: list[EvidenceUnit],
) -> str:
    segments = build_report_segments(report)
    source_by_url = {source.url: source for source in sources}
    report_sources = []
    for source in report.sources:
        normalized_url = normalize_source_url(source.url)
        inventory_source = source_by_url[normalized_url]
        report_sources.append(
            {
                "source_id": inventory_source.source_id,
                "title": inventory_source.title,
                "url": normalized_url,
                "published_at": inventory_source.published_at,
                "source_type": inventory_source.source_type,
            }
        )

    pre_run = state.get("pre_synthesis_evaluation_run")
    pre_evaluation = (
        pre_run.evaluation
        if pre_run is not None
        and getattr(pre_run, "status", None) == "completed"
        and getattr(pre_run, "evaluation", None) is not None
        else None
    )
    payload = {
        "candidate_report_sources": report_sources,
        "evidence_dossier": {
            "evidence_units": [unit.model_dump() for unit in evidence],
            "sources": [source.model_dump() for source in sources],
            "verification_boundary": (
                "Only source_excerpt evidence units are direct, single-source citation "
                "candidates. derived_summary units are diagnostic context without source-level "
                "attribution and cannot support material report claims."
            ),
        },
        "pre_synthesis_open_questions": (
            list(pre_evaluation.unresolved_questions) if pre_evaluation is not None else []
        ),
        "report_segments": [segment.model_dump() for segment in segments],
        "research_contract": {
            "depth": state.get("depth", "standard"),
            "sub_questions": [
                {
                    "id": question.id,
                    "priority": question.priority,
                    "question": question.question,
                    "rationale": question.rationale,
                }
                for question in questions
            ],
            "topic": state.get("topic", ""),
        },
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def validate_post_synthesis_reference_contract(
    evaluation: PostSynthesisEvaluation,
    *,
    report: ResearchReport,
    active_ids: list[str],
    sources: list[EvidenceSource],
    evidence: list[EvidenceUnit],
) -> None:
    segments = build_report_segments(report)
    segments_by_id = {segment.id: segment for segment in segments}
    known_segment_ids = set(segments_by_id)
    audit_ids = [audit.segment_id for audit in evaluation.segment_audits]
    if len(audit_ids) != len(set(audit_ids)) or set(audit_ids) != known_segment_ids:
        raise _InvalidPostEvaluationReferences(
            "segment audits must exactly match every candidate report segment"
        )

    sources_by_id = {source.source_id: source for source in sources}
    evidence_by_id = {unit.evidence_id: unit for unit in evidence}
    direct_evidence_by_id = {
        unit.evidence_id: unit
        for unit in evidence
        if unit.provenance == "source_excerpt"
    }
    if not sources_by_id or not direct_evidence_by_id:
        raise _InvalidPostEvaluationReferences(
            "direct source excerpt evidence is required"
        )

    known_source_urls = {source.url for source in sources}
    for source in report.sources:
        normalized_url = normalize_source_url(source.url)
        if not normalized_url or normalized_url not in known_source_urls:
            raise _InvalidPostEvaluationReferences(
                "candidate bibliography references a source outside the evidence dossier"
            )

    markers_by_segment: dict[str, tuple[set[str], set[str]]] = {}
    for segment in segments:
        evidence_marker_ids: set[str] = set()
        source_marker_ids: set[str] = set()
        for marker_type, marker_id in _CITATION_MARKER_RE.findall(segment.text):
            if not _CITATION_ID_RE.fullmatch(marker_id):
                raise _InvalidPostEvaluationReferences(
                    "candidate report contains a malformed citation marker"
                )
            if marker_type == "E":
                if marker_id not in direct_evidence_by_id:
                    raise _InvalidPostEvaluationReferences(
                        "candidate report cites non-direct or unknown evidence"
                    )
                evidence_marker_ids.add(marker_id)
            else:
                if marker_id not in sources_by_id:
                    raise _InvalidPostEvaluationReferences(
                        "candidate report cites an unknown source"
                    )
                source_marker_ids.add(marker_id)
        for evidence_id in evidence_marker_ids:
            bound_source_id = direct_evidence_by_id[evidence_id].source_ids[0]
            if bound_source_id not in source_marker_ids:
                raise _InvalidPostEvaluationReferences(
                    "every evidence marker requires its bound source marker in the same segment"
                )
        for source_id in source_marker_ids:
            if not any(
                direct_evidence_by_id[evidence_id].source_ids == [source_id]
                for evidence_id in evidence_marker_ids
            ):
                raise _InvalidPostEvaluationReferences(
                    "every source marker must bind to direct evidence in the same segment"
                )
        markers_by_segment[segment.id] = (
            evidence_marker_ids,
            source_marker_ids,
        )

    active_id_set = set(active_ids)
    known_claim_ids: set[str] = set()
    for audit in evaluation.segment_audits:
        segment = segments_by_id[audit.segment_id]
        segment_evidence_ids, segment_source_ids = markers_by_segment[audit.segment_id]
        for claim in audit.claims:
            if claim.claim_id in known_claim_ids:
                raise _InvalidPostEvaluationReferences("claim IDs must be globally unique")
            known_claim_ids.add(claim.claim_id)
            if claim.claim_text not in segment.text:
                raise _InvalidPostEvaluationReferences(
                    "claim_text must be copied exactly from its audited candidate segment"
                )
            reference_ids = [reference.evidence_id for reference in claim.evidence_refs]
            if len(reference_ids) != len(set(reference_ids)):
                raise _InvalidPostEvaluationReferences(
                    "claim evidence references must be unique"
                )
            referenced_source_ids: set[str] = set()
            for reference in claim.evidence_refs:
                unit = evidence_by_id.get(reference.evidence_id)
                if unit is None:
                    raise _InvalidPostEvaluationReferences(
                        "claim references an unknown evidence ID"
                    )
                if reference.supporting_excerpt not in unit.text:
                    raise _InvalidPostEvaluationReferences(
                        "supporting excerpt must be copied exactly from its evidence unit"
                    )
                if reference.evidence_id not in segment_evidence_ids:
                    raise _InvalidPostEvaluationReferences(
                        "claim evidence marker must occur in its audited segment"
                    )
                if unit.provenance == "source_excerpt":
                    referenced_source_ids.update(unit.source_ids)
                elif claim.support == "supported":
                    raise _InvalidPostEvaluationReferences(
                        "a supported claim requires direct source excerpt evidence"
                    )
            if set(claim.citation.cited_source_ids) - set(sources_by_id):
                raise _InvalidPostEvaluationReferences(
                    "claim citation references an unknown source ID"
                )
            cited_source_ids = set(claim.citation.cited_source_ids)
            if cited_source_ids - segment_source_ids:
                raise _InvalidPostEvaluationReferences(
                    "claim source marker must occur in its audited segment"
                )
            if cited_source_ids - referenced_source_ids:
                raise _InvalidPostEvaluationReferences(
                    "claim citation is not attached to its referenced evidence"
                )
            if claim.support == "supported" and (
                not claim.evidence_refs
                or not cited_source_ids
                or cited_source_ids != referenced_source_ids
            ):
                raise _InvalidPostEvaluationReferences(
                    "a supported claim requires direct evidence and every bound source"
                )

    issue_ids: set[str] = set()
    for issue in evaluation.issues:
        if issue.id in issue_ids:
            raise _InvalidPostEvaluationReferences("issue IDs must be unique")
        issue_ids.add(issue.id)
        if set(issue.segment_ids) - known_segment_ids:
            raise _InvalidPostEvaluationReferences("issue references an unknown segment ID")
        if set(issue.claim_ids) - known_claim_ids:
            raise _InvalidPostEvaluationReferences("issue references an unknown claim ID")
        if set(issue.affected_sub_question_ids) - active_id_set:
            raise _InvalidPostEvaluationReferences(
                "issue references an unknown sub-question ID"
            )


async def _invoke_post_synthesis_evaluation(structured_llm, messages):
    return await asyncio.wait_for(
        structured_llm.ainvoke(messages),
        timeout=POST_EVALUATION_TIMEOUT_SECONDS,
    )


def _classify_error(exc: Exception) -> PostEvaluationErrorCode:
    if isinstance(exc, _MissingPostEvaluationInputs):
        return "missing_inputs"
    if isinstance(exc, _SectionGenerationFailure):
        return "section_generation_failure"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(exc, _InvalidPostEvaluationReferences):
        return "invalid_references"
    if isinstance(exc, (OutputParserException, ValidationError, TypeError, ValueError)):
        return "invalid_output"
    return "provider_error"


def _evaluator_model(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> str:
    return resolved_model_name(
        state,
        runtime=runtime,
        default=DEFAULT_POST_EVALUATOR_MODEL,
    )


def _run_update(
    state: DeepResearchState,
    run: PostSynthesisEvaluationRun,
) -> dict:
    history = list(state.get("post_evaluation_history", []))
    history.append(run)
    return {
        "post_synthesis_evaluation_run": run,
        "post_evaluation_history": history,
    }


def _failed_run(
    state: DeepResearchState,
    error_code: PostEvaluationErrorCode,
    started_at: float,
    *,
    evaluator_model: str,
    attempts: int,
    report_subject_digest: str | None = None,
    report_subject_version: int | None = None,
) -> dict:
    return _run_update(
        state,
        PostSynthesisEvaluationRun(
            status="failed",
            evaluation=None,
            error_code=error_code,
            report_digest=report_subject_digest,
            report_version=report_subject_version,
            evaluator_model=evaluator_model,
            attempts=attempts,
            duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
        ),
    )


async def post_synthesis_evaluate_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    """Run a bounded structured-LLM audit of every candidate report surface."""
    started_at = time.monotonic()
    evaluator_model = _evaluator_model(state, runtime)
    report_subject_digest: str | None = None
    report_subject_version: int | None = None
    raw_budget = state.get("budget_snapshot")
    try:
        budget = (
            raw_budget
            if isinstance(raw_budget, BudgetSnapshot)
            else BudgetSnapshot.model_validate(raw_budget or {})
        )
    except (TypeError, ValueError, ValidationError):
        budget = None

    # The controller accounts for each completed evaluation round.  Check the
    # persisted ledger before constructing or invoking the LLM so a repaired
    # candidate cannot consume one call beyond the configured hard limit.
    if budget is not None and (
        budget.post_evaluations_used >= budget.post_evaluation_limit
    ):
        try:
            report_subject_digest = report_digest(_candidate_report(state))
            report_subject_version = _report_version(state)
        except Exception:
            pass
        logger.info(
            "post_synthesis_evaluation_skipped_budget_exhausted",
            used=budget.post_evaluations_used,
            limit=budget.post_evaluation_limit,
        )
        return _failed_run(
            state,
            "budget_exhausted",
            started_at,
            evaluator_model=evaluator_model,
            attempts=0,
            report_subject_digest=report_subject_digest,
            report_subject_version=report_subject_version,
        )
    try:
        report, questions, _reports, sources, evidence = _validate_inputs(state)
        report_subject_version = _report_version(state)
        report_subject_digest = report_digest(report)
        active_ids = [question.id for question in questions]
        audit_json = _build_post_synthesis_audit_json(
            state,
            report=report,
            questions=questions,
            sources=sources,
            evidence=evidence,
        )
        structured_llm = make_structured_llm(
            state,
            PostSynthesisEvaluation,
            runtime=runtime,
            max_tokens=7000,
            temperature=0.0,
        )
        messages = [
            {"role": "system", "content": POST_SYNTHESIS_EVALUATE_SYSTEM},
            {
                "role": "user",
                "content": POST_SYNTHESIS_EVALUATE_USER.format(
                    post_synthesis_audit_json=audit_json,
                ),
            },
        ]
    except Exception as exc:
        error_code = _classify_error(exc)
        logger.warning(
            "post_synthesis_evaluation_setup_failed",
            error_code=error_code,
            error_type=type(exc).__name__,
        )
        return _failed_run(
            state,
            error_code,
            started_at,
            evaluator_model=evaluator_model,
            attempts=0,
            report_subject_digest=report_subject_digest,
            report_subject_version=report_subject_version,
        )

    last_error: Exception | None = None
    for attempt in range(1, MAX_POST_EVALUATION_ATTEMPTS + 1):
        try:
            raw_evaluation = await _invoke_post_synthesis_evaluation(
                structured_llm,
                messages,
            )
            evaluation = PostSynthesisEvaluation.model_validate(raw_evaluation)
            validate_post_synthesis_reference_contract(
                evaluation,
                report=report,
                active_ids=active_ids,
                sources=sources,
                evidence=evidence,
            )
            run = PostSynthesisEvaluationRun(
                status="completed",
                evaluation=evaluation,
                error_code=None,
                report_digest=report_subject_digest,
                report_version=report_subject_version,
                evaluator_model=evaluator_model,
                attempts=attempt,
                duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
            )
            logger.info(
                "post_synthesis_evaluation_completed",
                attempt=attempt,
                duration_ms=run.duration_ms,
                issue_count=len(evaluation.issues),
                scores=evaluation.scores.model_dump(),
            )
            return _run_update(state, run)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "post_synthesis_evaluation_attempt_failed",
                attempt=attempt,
                error_code=_classify_error(exc),
                error_type=type(exc).__name__,
            )

    assert last_error is not None
    return _failed_run(
        state,
        _classify_error(last_error),
        started_at,
        evaluator_model=evaluator_model,
        attempts=MAX_POST_EVALUATION_ATTEMPTS,
        report_subject_digest=report_subject_digest,
        report_subject_version=report_subject_version,
    )


__all__ = [
    "build_report_segments",
    "post_synthesis_evaluate_node",
    "validate_post_synthesis_reference_contract",
]
