from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Literal

import structlog
from langchain_core.exceptions import OutputParserException
from langgraph.runtime import Runtime
from pydantic import ValidationError

from app.deep_research.context import DeepResearchContext
from app.deep_research.llm_factory import make_structured_llm
from app.deep_research.models import (
    EvidenceSource,
    EvidenceUnit,
    PostSynthesisEvaluationRun,
    ReportEvaluationIssue,
    ReportRevisionPatch,
    ReportSegment,
    ResearchReport,
)
from app.deep_research.prompts import REPORT_REVISION_SYSTEM, REPORT_REVISION_USER
from app.deep_research.provenance import build_evidence_inventory, build_report_segments
from app.deep_research.state import DeepResearchState

logger = structlog.get_logger()

REPORT_REVISION_TIMEOUT_SECONDS = 90
MAX_REPORT_REVISION_ATTEMPTS = 2

RevisionErrorCode = Literal[
    "report_revision_invalid_inputs",
    "report_revision_timeout",
    "report_revision_provider_error",
    "report_revision_invalid_output",
    "report_revision_invalid_references",
]

_CITATION_MARKER_RE = re.compile(r"\[(E|S):([^\]]+)\]")
_CITATION_ID_RE = re.compile(r"(?:ev|src)-[0-9a-f]{16}")


class _MissingRevisionInputs(ValueError):
    pass


class _InvalidRevisionReferences(ValueError):
    pass


def _candidate_report(state: DeepResearchState) -> ResearchReport:
    raw_report = state.get("candidate_report")
    if isinstance(raw_report, ResearchReport):
        return raw_report
    if isinstance(raw_report, dict):
        try:
            return ResearchReport.model_validate(raw_report)
        except ValidationError as exc:
            raise _MissingRevisionInputs("candidate report is invalid") from exc
    raise _MissingRevisionInputs("candidate report is required")


def _completed_evaluation_run(
    state: DeepResearchState,
) -> PostSynthesisEvaluationRun:
    raw_run = state.get("post_synthesis_evaluation_run")
    if raw_run is None:
        raise _MissingRevisionInputs("post-synthesis evaluation is required")
    try:
        run = PostSynthesisEvaluationRun.model_validate(raw_run)
    except ValidationError as exc:
        raise _MissingRevisionInputs("post-synthesis evaluation is invalid") from exc
    if run.status != "completed" or run.evaluation is None:
        raise _MissingRevisionInputs("completed post-synthesis evaluation is required")
    return run


def _authorized_segments(
    state: DeepResearchState,
    report: ResearchReport,
) -> list[ReportSegment]:
    raw_target_ids = state.get("target_report_segment_ids")
    if not isinstance(raw_target_ids, list) or not raw_target_ids:
        raise _MissingRevisionInputs("authorized report segment IDs are required")
    if any(not isinstance(segment_id, str) or not segment_id.strip() for segment_id in raw_target_ids):
        raise _MissingRevisionInputs("authorized report segment IDs must be non-empty strings")
    target_ids = [segment_id.strip() for segment_id in raw_target_ids]
    if len(target_ids) != len(set(target_ids)):
        raise _MissingRevisionInputs("authorized report segment IDs must be unique")

    segments = build_report_segments(report)
    segments_by_id = {segment.id: segment for segment in segments}
    if unknown_ids := set(target_ids) - set(segments_by_id):
        raise _MissingRevisionInputs(
            "authorized report segment IDs must exist: " + ", ".join(sorted(unknown_ids))
        )
    target_id_set = set(target_ids)
    return [segment for segment in segments if segment.id in target_id_set]


def _authorized_issues(
    run: PostSynthesisEvaluationRun,
    segments: list[ReportSegment],
) -> list[ReportEvaluationIssue]:
    assert run.evaluation is not None
    target_ids = {segment.id for segment in segments}
    issues = [
        issue
        for issue in run.evaluation.issues
        if issue.suggested_repair_stage == "synthesis"
        and bool(set(issue.segment_ids) & target_ids)
    ]
    if not issues:
        raise _MissingRevisionInputs("no synthesis issue applies to the authorized segments")
    if any(not set(issue.segment_ids).issubset(target_ids) for issue in issues):
        raise _MissingRevisionInputs(
            "a synthesis issue cannot be partially repaired outside its authorized segments"
        )
    covered_segments = {
        segment_id
        for issue in issues
        for segment_id in issue.segment_ids
    }
    if covered_segments != target_ids:
        raise _MissingRevisionInputs(
            "every authorized segment must be covered by a synthesis issue"
        )
    return issues


def _evidence_dossier(
    state: DeepResearchState,
) -> tuple[list[EvidenceSource], list[EvidenceUnit]]:
    raw_reports = state.get("sub_reports", [])
    if not isinstance(raw_reports, list) or not raw_reports:
        raise _MissingRevisionInputs("retained evidence is required")
    try:
        sources, evidence = build_evidence_inventory(raw_reports)
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise _MissingRevisionInputs("retained evidence is invalid") from exc
    if not sources or not evidence:
        raise _MissingRevisionInputs("approved evidence dossier is empty")
    if not any(unit.provenance == "source_excerpt" for unit in evidence):
        raise _MissingRevisionInputs("direct source excerpt evidence is required")
    return sources, evidence


def _build_revision_json(
    state: DeepResearchState,
    *,
    segments: list[ReportSegment],
    issues: list[ReportEvaluationIssue],
    sources: list[EvidenceSource],
    evidence: list[EvidenceUnit],
) -> str:
    payload = {
        "schema_version": "report-revision-request.v1",
        "authorized_segment_ids": [segment.id for segment in segments],
        "authorized_segments": [segment.model_dump(mode="json") for segment in segments],
        "evidence_dossier": {
            "evidence_units": [unit.model_dump(mode="json") for unit in evidence],
            "sources": [source.model_dump(mode="json") for source in sources],
            "verification_boundary": (
                "Only source_excerpt evidence units are direct, single-source citation "
                "candidates. derived_summary units are diagnostic context and cannot support "
                "material report claims."
            ),
        },
        "issues": [issue.model_dump(mode="json") for issue in issues],
        "research_contract": {
            "topic": state.get("topic", ""),
            "depth": state.get("depth", "standard"),
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


async def _invoke_report_revision(structured_llm, messages) -> Any:
    return await asyncio.wait_for(
        structured_llm.ainvoke(messages),
        timeout=REPORT_REVISION_TIMEOUT_SECONDS,
    )


def _classify_error(exc: Exception) -> RevisionErrorCode:
    if isinstance(exc, _MissingRevisionInputs):
        return "report_revision_invalid_inputs"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "report_revision_timeout"
    if isinstance(exc, _InvalidRevisionReferences):
        return "report_revision_invalid_references"
    if isinstance(exc, (OutputParserException, ValidationError, TypeError, ValueError)):
        return "report_revision_invalid_output"
    return "report_revision_provider_error"


def _validate_revision_citations(
    segment: ReportSegment,
    text: str,
    *,
    evidence_by_id: dict[str, EvidenceUnit],
    source_ids: set[str],
) -> None:
    cited_evidence_ids: set[str] = set()
    cited_source_ids: set[str] = set()
    for marker_type, marker_id in _CITATION_MARKER_RE.findall(text):
        if not _CITATION_ID_RE.fullmatch(marker_id):
            raise _InvalidRevisionReferences("citation marker has an invalid identifier")
        if marker_type == "E":
            if marker_id not in evidence_by_id:
                raise _InvalidRevisionReferences("revision references unknown evidence")
            cited_evidence_ids.add(marker_id)
        else:
            if marker_id not in source_ids:
                raise _InvalidRevisionReferences("revision references an unknown source")
            cited_source_ids.add(marker_id)

    requires_citations = segment.component in {
        "executive_summary",
        "section",
        "key_finding",
    }
    if requires_citations and (not cited_evidence_ids or not cited_source_ids):
        raise _InvalidRevisionReferences(
            "revised factual report surfaces require evidence and source markers"
        )
    if segment.component == "title" and (cited_evidence_ids or cited_source_ids):
        raise _InvalidRevisionReferences("report title must not contain citation markers")

    for evidence_id in cited_evidence_ids:
        if not (set(evidence_by_id[evidence_id].source_ids) & cited_source_ids):
            raise _InvalidRevisionReferences(
                "cited evidence requires a linked source marker"
            )
    if cited_source_ids and not any(
        source_id in evidence_by_id[evidence_id].source_ids
        for evidence_id in cited_evidence_ids
        for source_id in cited_source_ids
    ):
        raise _InvalidRevisionReferences(
            "source markers must be linked to cited evidence"
        )


def _validate_patch(
    patch: ReportRevisionPatch,
    *,
    segments: list[ReportSegment],
    issues: list[ReportEvaluationIssue],
    sources: list[EvidenceSource],
    evidence: list[EvidenceUnit],
) -> dict[str, str]:
    target_ids = [segment.id for segment in segments]
    patch_ids = [update.segment_id for update in patch.updates]
    if set(patch_ids) != set(target_ids) or len(patch_ids) != len(target_ids):
        raise _InvalidRevisionReferences(
            "revision updates must exactly match authorized report segments"
        )

    issue_ids = [issue.id for issue in issues]
    if set(patch.resolved_issue_ids) != set(issue_ids) or len(
        patch.resolved_issue_ids
    ) != len(issue_ids):
        raise _InvalidRevisionReferences(
            "resolved issue IDs must exactly match authorized synthesis issues"
        )

    segments_by_id = {segment.id: segment for segment in segments}
    evidence_by_id = {
        unit.evidence_id: unit
        for unit in evidence
        if unit.provenance == "source_excerpt"
    }
    source_ids = {source.source_id for source in sources}
    revised_by_id = {
        update.segment_id: update.revised_text.strip()
        for update in patch.updates
    }
    for segment_id in target_ids:
        revised_text = revised_by_id[segment_id]
        segment = segments_by_id[segment_id]
        if not revised_text or revised_text == segment.text.strip():
            raise _InvalidRevisionReferences(
                "every authorized report segment must receive a non-empty change"
            )
        _validate_revision_citations(
            segment,
            revised_text,
            evidence_by_id=evidence_by_id,
            source_ids=source_ids,
        )
    return revised_by_id


def _apply_patch(
    report: ResearchReport,
    segments: list[ReportSegment],
    revised_by_id: dict[str, str],
) -> ResearchReport:
    """Apply text by stable location; headings and bibliography remain immutable."""
    report_data = report.model_dump(mode="python")
    for segment in segments:
        revised_text = revised_by_id[segment.id]
        if segment.component == "title":
            report_data["title"] = revised_text
        elif segment.component == "executive_summary":
            report_data["executive_summary"] = revised_text
        elif segment.component == "section":
            if segment.section_index is None:
                raise _InvalidRevisionReferences("section segment lacks an index")
            report_data["sections"][segment.section_index]["content"] = revised_text
        elif segment.component == "key_finding":
            if segment.item_index is None:
                raise _InvalidRevisionReferences("key-finding segment lacks an index")
            report_data["key_findings"][segment.item_index] = revised_text
        elif segment.component == "limitations":
            report_data["limitations"] = revised_text
        else:  # pragma: no cover - ReportSegment validates the closed component set.
            raise _InvalidRevisionReferences("unsupported report segment component")
    try:
        return ResearchReport.model_validate(report_data)
    except ValidationError as exc:
        raise _InvalidRevisionReferences("revised report is invalid") from exc


def _next_counter(state: DeepResearchState, key: str) -> int:
    current = state.get(key, 0)  # type: ignore[misc]
    if isinstance(current, bool) or not isinstance(current, int) or current < 0:
        raise _MissingRevisionInputs(f"{key} must be a non-negative integer")
    return current + 1


def _failed_revision(error_code: RevisionErrorCode) -> dict[str, Any]:
    return {
        "report_revision_status": "failed",
        "final_report": None,
        "report_accepted": False,
        "workflow_error_code": error_code,
        "terminal_reason": "The candidate report revision did not complete safely.",
    }


async def revise_report_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict[str, Any]:
    """Apply one bounded, synthesis-only patch to explicitly authorized segments."""
    started_at = time.monotonic()
    try:
        report = _candidate_report(state)
        evaluation_run = _completed_evaluation_run(state)
        segments = _authorized_segments(state, report)
        issues = _authorized_issues(evaluation_run, segments)
        sources, evidence = _evidence_dossier(state)
        revision_count = _next_counter(state, "report_revision_count")
        report_version = _next_counter(state, "report_version")
        revision_json = _build_revision_json(
            state,
            segments=segments,
            issues=issues,
            sources=sources,
            evidence=evidence,
        )
        structured_llm = make_structured_llm(
            state,
            ReportRevisionPatch,
            runtime=runtime,
            max_tokens=5000,
            temperature=0.0,
        )
        messages = [
            {"role": "system", "content": REPORT_REVISION_SYSTEM},
            {
                "role": "user",
                "content": REPORT_REVISION_USER.format(
                    report_revision_json=revision_json,
                ),
            },
        ]
    except Exception as exc:
        error_code = _classify_error(exc)
        logger.warning(
            "report_revision_setup_failed",
            error_code=error_code,
            error_type=type(exc).__name__,
        )
        return _failed_revision(error_code)

    last_error: Exception | None = None
    for attempt in range(1, MAX_REPORT_REVISION_ATTEMPTS + 1):
        try:
            raw_patch = await _invoke_report_revision(structured_llm, messages)
            patch = ReportRevisionPatch.model_validate(raw_patch)
            revised_by_id = _validate_patch(
                patch,
                segments=segments,
                issues=issues,
                sources=sources,
                evidence=evidence,
            )
            revised_report = _apply_patch(report, segments, revised_by_id)
            update: dict[str, Any] = {
                "candidate_report": revised_report,
                "final_report": None,
                "report_accepted": False,
                "report_revision_count": revision_count,
                "report_version": report_version,
                "report_revision_status": "completed",
                "target_report_segment_ids": [],
                "post_synthesis_evaluation_run": None,
                "post_synthesis_controller_decision": None,
                "workflow_error_code": None,
                "terminal_reason": None,
                "terminal_status": None,
            }
            history = state.get("candidate_report_history")  # type: ignore[typeddict-item]
            if isinstance(history, list):
                update["candidate_report_history"] = [*history, report]
            logger.info(
                "report_revision_completed",
                attempt=attempt,
                duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
                report_version=report_version,
                revised_segment_count=len(segments),
            )
            return update
        except Exception as exc:
            last_error = exc
            logger.warning(
                "report_revision_attempt_failed",
                attempt=attempt,
                error_code=_classify_error(exc),
                error_type=type(exc).__name__,
            )

    assert last_error is not None
    return _failed_revision(_classify_error(last_error))


def route_after_report_revision(state: DeepResearchState) -> str:
    """Only a validated completed patch may be audited again."""
    return (
        "evaluate_report"
        if state.get("report_revision_status") == "completed"
        else "stop_incomplete"
    )


__all__ = [
    "MAX_REPORT_REVISION_ATTEMPTS",
    "REPORT_REVISION_TIMEOUT_SECONDS",
    "revise_report_node",
    "route_after_report_revision",
]
