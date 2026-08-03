from __future__ import annotations

import asyncio
import json
import time
from typing import Literal

import structlog
from langchain_core.exceptions import OutputParserException
from langgraph.runtime import Runtime
from pydantic import ValidationError

from app.deep_research.context import DeepResearchContext
from app.deep_research.llm_factory import make_structured_llm, resolved_model_name
from app.deep_research.models import (
    PreSynthesisEvaluation,
    PreSynthesisEvaluationRun,
)
from app.deep_research.prompts import (
    PRE_SYNTHESIS_EVALUATE_SYSTEM,
    PRE_SYNTHESIS_EVALUATE_USER,
)
from app.deep_research.provenance import (
    normalize_source_url,
    sanitize_source_excerpt,
)
from app.deep_research.state import DeepResearchState

logger = structlog.get_logger()

EVALUATION_TIMEOUT_SECONDS = 90
MAX_EVALUATION_ATTEMPTS = 2
DEFAULT_EVALUATOR_MODEL = "deepseek-v4-pro"

EvaluationErrorCode = Literal[
    "missing_inputs",
    "timeout",
    "provider_error",
    "invalid_output",
    "invalid_references",
]


class _MissingEvaluationInputs(ValueError):
    pass


class _InvalidEvaluationReferences(ValueError):
    pass


def _validate_active_inputs(state: DeepResearchState) -> tuple[list[str], set[str]]:
    topic = state.get("topic", "").strip()
    sub_questions = state.get("sub_questions", [])
    sub_reports = state.get("sub_reports", [])

    if not topic or not sub_questions or not sub_reports:
        raise _MissingEvaluationInputs(
            "topic, active sub-questions, and at least one sub-report are required"
        )

    active_ids = [sub_question.id for sub_question in sub_questions]
    if any(not sub_question_id.strip() for sub_question_id in active_ids):
        raise _MissingEvaluationInputs("active sub-question IDs must not be empty")
    if len(active_ids) != len(set(active_ids)):
        raise _MissingEvaluationInputs("active sub-question IDs must be unique")

    active_id_set = set(active_ids)
    report_ids = [report.sub_question_id for report in sub_reports]
    if any(not report_id.strip() for report_id in report_ids):
        raise _MissingEvaluationInputs("sub-report IDs must not be empty")
    if len(report_ids) != len(set(report_ids)):
        raise _MissingEvaluationInputs("sub-report IDs must be unique")
    if unknown_report_ids := set(report_ids) - active_id_set:
        raise _MissingEvaluationInputs(
            "sub-reports reference inactive IDs: " + ", ".join(sorted(unknown_report_ids))
        )

    failed_ids = {
        failed.get("sub_question_id", "")
        for failed in state.get("failed_queries", [])
    }
    if "" in failed_ids or failed_ids - active_id_set:
        raise _MissingEvaluationInputs("failed queries must reference active sub-question IDs")

    source_urls = {
        normalized_url
        for report in sub_reports
        for source in report.sources
        if (normalized_url := normalize_source_url(source.url))
    }
    return active_ids, source_urls


def _build_research_corpus_json(state: DeepResearchState) -> str:
    """Serialize a deterministic whitelist; credentials and workspace state stay out."""
    payload = {
        "depth": state.get("depth", "standard"),
        "failed_queries": [
            {
                "error_code": failed.get("error_code", "unknown"),
                "query": list(failed.get("query", [])),
                "sub_question_id": failed.get("sub_question_id", ""),
            }
            for failed in state.get("failed_queries", [])
        ],
        "sub_questions": [
            {
                "id": sub_question.id,
                "priority": sub_question.priority,
                "question": sub_question.question,
                "rationale": sub_question.rationale,
                "search_queries": list(sub_question.search_queries),
            }
            for sub_question in state.get("sub_questions", [])
        ],
        "sub_reports": [
            {
                "confidence": report.confidence,
                "findings": report.findings,
                "gaps": report.gaps,
                "key_facts": list(report.key_facts),
                "question": report.question,
                "sources": [
                    {
                        "excerpt": sanitize_source_excerpt(source.excerpt),
                        "published_at": sanitize_source_excerpt(
                            source.published_at,
                            max_chars=100,
                        ) or None,
                        "source_type": sanitize_source_excerpt(
                            source.source_type,
                            max_chars=64,
                        ) or None,
                        "title": sanitize_source_excerpt(
                            source.title,
                            max_chars=500,
                        ),
                        "url": normalize_source_url(source.url),
                    }
                    for source in report.sources
                    if normalize_source_url(source.url)
                ],
                "sub_question_id": report.sub_question_id,
            }
            for report in state.get("sub_reports", [])
        ],
        "topic": state.get("topic", ""),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_evaluation_references(
    evaluation: PreSynthesisEvaluation,
    *,
    active_ids: list[str],
    source_urls: set[str],
) -> None:
    active_id_set = set(active_ids)
    if set(evaluation.assessed_sub_question_ids) != active_id_set:
        raise _InvalidEvaluationReferences(
            "assessed_sub_question_ids must exactly match the active plan"
        )

    for issue in evaluation.issues:
        if unknown_ids := set(issue.affected_sub_question_ids) - active_id_set:
            raise _InvalidEvaluationReferences(
                f"issue {issue.id} references inactive sub-question IDs: "
                + ", ".join(sorted(unknown_ids))
            )
        if unknown_urls := set(issue.source_urls) - source_urls:
            raise _InvalidEvaluationReferences(
                f"issue {issue.id} references unknown source URLs: "
                + ", ".join(sorted(unknown_urls))
            )

    for directive in evaluation.repair_directives:
        if unknown_ids := set(directive.target_sub_question_ids) - active_id_set:
            raise _InvalidEvaluationReferences(
                f"repair directive {directive.id} references inactive sub-question IDs: "
                + ", ".join(sorted(unknown_ids))
            )


async def _invoke_pre_synthesis_evaluation(structured_llm, messages):
    return await asyncio.wait_for(
        structured_llm.ainvoke(messages),
        timeout=EVALUATION_TIMEOUT_SECONDS,
    )


def _classify_evaluation_error(exc: Exception) -> EvaluationErrorCode:
    if isinstance(exc, _MissingEvaluationInputs):
        return "missing_inputs"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(exc, _InvalidEvaluationReferences):
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
        default=DEFAULT_EVALUATOR_MODEL,
    )


def _failed_run(
    error_code: EvaluationErrorCode,
    started_at: float,
    *,
    evaluator_model: str,
    attempts: int,
) -> dict:
    return {
        "pre_synthesis_evaluation_run": PreSynthesisEvaluationRun(
            status="failed",
            evaluation=None,
            error_code=error_code,
            evaluator_model=evaluator_model,
            attempts=attempts,
            duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
        )
    }


async def evidence_evaluate_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    """Use an LLM for semantic diagnosis while leaving routing to the controller."""
    started_at = time.monotonic()
    evaluator_model = _evaluator_model(state, runtime)

    try:
        active_ids, source_urls = _validate_active_inputs(state)
    except _MissingEvaluationInputs as exc:
        logger.warning(
            "pre_synthesis_evaluation_missing_inputs",
            error_type=type(exc).__name__,
        )
        return _failed_run(
            "missing_inputs",
            started_at,
            evaluator_model=evaluator_model,
            attempts=0,
        )

    try:
        research_corpus_json = _build_research_corpus_json(state)
        messages = [
            {"role": "system", "content": PRE_SYNTHESIS_EVALUATE_SYSTEM},
            {
                "role": "user",
                "content": PRE_SYNTHESIS_EVALUATE_USER.format(
                    research_corpus_json=research_corpus_json,
                ),
            },
        ]
        structured_llm = make_structured_llm(
            state,
            PreSynthesisEvaluation,
            runtime=runtime,
            max_tokens=3500,
            temperature=0.0,
        )
    except Exception as exc:
        error_code = _classify_evaluation_error(exc)
        logger.error(
            "pre_synthesis_evaluation_setup_failed",
            error_code=error_code,
            error_type=type(exc).__name__,
        )
        return _failed_run(
            error_code,
            started_at,
            evaluator_model=evaluator_model,
            attempts=0,
        )

    last_error: Exception | None = None
    for attempt in range(1, MAX_EVALUATION_ATTEMPTS + 1):
        try:
            raw_evaluation = await _invoke_pre_synthesis_evaluation(
                structured_llm,
                messages,
            )
            evaluation = PreSynthesisEvaluation.model_validate(raw_evaluation)
            _validate_evaluation_references(
                evaluation,
                active_ids=active_ids,
                source_urls=source_urls,
            )

            run = PreSynthesisEvaluationRun(
                status="completed",
                evaluation=evaluation,
                error_code=None,
                evaluator_model=evaluator_model,
                attempts=attempt,
                duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
            )
            logger.info(
                "pre_synthesis_evaluation_completed",
                attempt=attempt,
                duration_ms=run.duration_ms,
                issue_count=len(evaluation.issues),
                scores=evaluation.scores.model_dump(),
            )
            return {"pre_synthesis_evaluation_run": run}
        except Exception as exc:
            last_error = exc
            logger.warning(
                "pre_synthesis_evaluation_attempt_failed",
                attempt=attempt,
                error_code=_classify_evaluation_error(exc),
                error_type=type(exc).__name__,
            )

    assert last_error is not None
    error_code = _classify_evaluation_error(last_error)
    logger.error(
        "pre_synthesis_evaluation_failed",
        attempts=MAX_EVALUATION_ATTEMPTS,
        error_code=error_code,
    )
    return _failed_run(
        error_code,
        started_at,
        evaluator_model=evaluator_model,
        attempts=MAX_EVALUATION_ATTEMPTS,
    )
