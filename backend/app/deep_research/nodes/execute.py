from __future__ import annotations

import asyncio
import time
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from langchain_core.callbacks import adispatch_custom_event
from langgraph.runtime import Runtime

from app.deep_research.config import FETCH_TOP_N
from app.deep_research.context import DeepResearchContext
from app.deep_research.llm_factory import make_structured_llm
from app.deep_research.models import SubQuestion, SubReport, SourceRef
from app.deep_research.prompts import EXECUTE_SYSTEM, EXECUTE_USER
from app.deep_research.provenance import (
    MAX_SOURCE_EXCERPT_CHARS,
    normalize_source_url,
    sanitize_source_excerpt,
    source_id_for_url,
)
from app.deep_research.state import DeepResearchState, FailedQuery, merge_sub_reports
from app.deep_research.tools.search import tavily_search
from app.deep_research.tools.fetch import fetch_pages

logger = structlog.get_logger()


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def _llm_summarize(structured_llm, messages):
    return await structured_llm.ainvoke(messages)


async def _execute_single(
    sub_q: SubQuestion,
    sq_index: int,
    sq_total: int,
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> tuple[SubReport | None, FailedQuery | None]:
    try:
        t_sq = time.monotonic()

        await adispatch_custom_event("execute_progress", {
            "event": "sq_start",
            "sub_question_id": sub_q.id,
            "sq_index": sq_index, "sq_total": sq_total,
            "question": sub_q.question,
            "message": f"Investigating: {sub_q.question[:80]}",
        })

        await adispatch_custom_event("execute_progress", {
            "event": "searching",
            "sub_question_id": sub_q.id,
            "sq_index": sq_index, "sq_total": sq_total,
            "queries": sub_q.search_queries[:3],
            "message": f"Searching: {sub_q.search_queries[0][:60]}",
        })

        search_results = await tavily_search(sub_q.search_queries)

        if not search_results:
            await adispatch_custom_event("execute_progress", {
                "event": "sq_complete",
                "sub_question_id": sub_q.id,
                "sq_index": sq_index, "sq_total": sq_total,
                "question": sub_q.question,
                "confidence": 0.0,
                "error": "no_results",
                "duration_ms": round((time.monotonic() - t_sq) * 1000),
                "message": f"No results: {sub_q.question[:60]}",
            })
            fallback = SubReport(
                sub_question_id=sub_q.id,
                question=sub_q.question,
                findings="No search results were found for this sub-question.",
                key_facts=["No data available"],
                confidence=0.0,
                gaps="Complete lack of search results; unable to investigate this question.",
                sources=[],
            )
            previous_report = next(
                (
                    report
                    for report in state.get("sub_reports", [])
                    if report.sub_question_id == sub_q.id
                ),
                None,
            )
            return (None if previous_report is not None else fallback), {
                "sub_question_id": sub_q.id,
                "query": sub_q.search_queries,
                "error_code": "no_results",
                "reason": "No search results were available for this research step.",
            }

        await adispatch_custom_event("execute_progress", {
            "event": "reading",
            "sub_question_id": sub_q.id,
            "sq_index": sq_index, "sq_total": sq_total,
            "results_count": len(search_results),
            "message": f"Reading {min(FETCH_TOP_N, len(search_results))} sources...",
        })

        top_urls = [r["url"] for r in search_results[:FETCH_TOP_N]]
        fetched = await fetch_pages(top_urls)

        context_parts: list[str] = []
        source_refs: list[SourceRef] = []

        for result in search_results[:FETCH_TOP_N]:
            url = result["url"]
            safe_url = normalize_source_url(url)
            title = sanitize_source_excerpt(result.get("title", ""), max_chars=500)
            snippet = sanitize_source_excerpt(
                result.get("snippet", ""),
                max_chars=MAX_SOURCE_EXCERPT_CHARS,
            )

            page_content = ""
            for furl, fcontent in fetched:
                if furl == url and fcontent:
                    page_content = fcontent
                    break

            retained_excerpt = sanitize_source_excerpt(
                page_content or snippet,
                max_chars=MAX_SOURCE_EXCERPT_CHARS,
            )
            prompt_content = sanitize_source_excerpt(
                page_content or snippet,
                max_chars=3000,
            )
            block = f"### {title}\nURL: {safe_url or '[invalid source URL]'}\n"
            if page_content and prompt_content:
                block += f"Content:\n{prompt_content}\n"
            elif snippet:
                block += f"Snippet:\n{snippet}\n"
            context_parts.append(block)
            if safe_url:
                published_at = sanitize_source_excerpt(
                    result.get("published_at") or result.get("published_date"),
                    max_chars=100,
                ) or None
                source_type = sanitize_source_excerpt(
                    result.get("source_type"),
                    max_chars=64,
                ) or ("fetched_page" if page_content else "search_snippet")
                source_refs.append(
                    SourceRef(
                        source_id=source_id_for_url(safe_url),
                        url=safe_url,
                        title=title,
                        excerpt=retained_excerpt,
                        published_at=published_at,
                        source_type=source_type,
                    )
                )

        for result in search_results[FETCH_TOP_N:]:
            safe_url = normalize_source_url(result.get("url", ""))
            title = sanitize_source_excerpt(result.get("title", ""), max_chars=500)
            snippet = sanitize_source_excerpt(
                result.get("snippet", ""),
                max_chars=MAX_SOURCE_EXCERPT_CHARS,
            )
            if snippet:
                context_parts.append(
                    f"### {title}\nURL: {safe_url or '[invalid source URL]'}\n"
                    f"Snippet: {snippet}\n"
                )
                if safe_url:
                    published_at = sanitize_source_excerpt(
                        result.get("published_at") or result.get("published_date"),
                        max_chars=100,
                    ) or None
                    source_type = sanitize_source_excerpt(
                        result.get("source_type"),
                        max_chars=64,
                    ) or "search_snippet"
                    source_refs.append(
                        SourceRef(
                            source_id=source_id_for_url(safe_url),
                            url=safe_url,
                            title=title,
                            excerpt=snippet,
                            published_at=published_at,
                            source_type=source_type,
                        )
                    )

        search_context = "\n---\n".join(context_parts)

        await adispatch_custom_event("execute_progress", {
            "event": "summarizing",
            "sub_question_id": sub_q.id,
            "sq_index": sq_index, "sq_total": sq_total,
            "message": f"Analyzing findings for: {sub_q.question[:60]}",
        })

        user_msg = EXECUTE_USER.format(
            question=sub_q.question,
            search_context=search_context,
        )

        structured_llm = make_structured_llm(
            state,
            SubReport,
            runtime=runtime,
            max_tokens=3000,
            temperature=0.2,
        )

        report: SubReport = await _llm_summarize(
            structured_llm,
            [
                {"role": "system", "content": EXECUTE_SYSTEM},
                {"role": "user", "content": user_msg},
            ]
        )
        report.sub_question_id = sub_q.id
        report.question = sub_q.question
        report.sources = source_refs

        await adispatch_custom_event("execute_progress", {
            "event": "sq_complete",
            "sub_question_id": sub_q.id,
            "sq_index": sq_index, "sq_total": sq_total,
            "question": sub_q.question,
            "confidence": report.confidence,
            "sources_count": len(source_refs),
            "duration_ms": round((time.monotonic() - t_sq) * 1000),
            "message": f"Done: {sub_q.question[:60]}",
        })

        logger.info(
            "execute_sub_question_done",
            sub_question_id=sub_q.id,
            confidence=report.confidence,
        )
        return report, None

    except Exception as exc:
        logger.warning(
            "execute_sub_question_failed",
            sub_question_id=sub_q.id,
            error_type=type(exc).__name__,
        )
        await adispatch_custom_event("execute_progress", {
            "event": "sq_complete",
            "sub_question_id": sub_q.id,
            "sq_index": sq_index, "sq_total": sq_total,
            "question": sub_q.question,
            "confidence": 0.0,
            "error": "execution_error",
            "duration_ms": round((time.monotonic() - t_sq) * 1000),
            "message": f"Failed: {sub_q.question[:60]}",
        })
        fallback = SubReport(
            sub_question_id=sub_q.id,
            question=sub_q.question,
            findings="This research step could not be completed.",
            key_facts=["Investigation failed"],
            confidence=0.0,
            gaps="Complete failure to investigate this sub-question.",
            sources=[],
        )
        previous_report = next(
            (
                report
                for report in state.get("sub_reports", [])
                if report.sub_question_id == sub_q.id
            ),
            None,
        )
        return (None if previous_report is not None else fallback), {
            "sub_question_id": sub_q.id,
            "query": sub_q.search_queries,
            "error_code": "execution_error",
            "reason": "execution_error",
        }


def select_execution_batch(
    sub_questions: list[SubQuestion],
    target_ids: list[str] | None,
) -> list[SubQuestion]:
    """Select a validated one-shot batch while retaining plan order."""
    active_ids = [sub_question.id for sub_question in sub_questions]
    if not active_ids or any(not question_id for question_id in active_ids):
        raise ValueError("active sub-question IDs must be non-empty")
    if len(active_ids) != len(set(active_ids)):
        raise ValueError("active sub-question IDs must be unique")
    if target_ids is None:
        return list(sub_questions)

    if not target_ids:
        raise ValueError("execution target IDs must be non-empty")
    if any(not target_id for target_id in target_ids):
        raise ValueError("execution target IDs must be non-empty")
    if len(target_ids) != len(set(target_ids)):
        raise ValueError("execution target IDs must be unique")
    if unknown_ids := set(target_ids) - set(active_ids):
        raise ValueError(
            "execution target IDs must be active: " + ", ".join(sorted(unknown_ids))
        )
    target_id_set = set(target_ids)
    return [sub_q for sub_q in sub_questions if sub_q.id in target_id_set]


def replace_failures_for_execution(
    existing: list[FailedQuery] | None,
    executed_ids: list[str],
    updates: list[FailedQuery],
) -> list[FailedQuery]:
    """Replace failure state only for sub-questions executed in this batch."""
    executed_id_set = set(executed_ids)
    retained = [
        failure
        for failure in existing or []
        if failure.get("sub_question_id") not in executed_id_set
    ]
    return retained + updates


async def execute_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    try:
        sub_questions = select_execution_batch(
            state.get("sub_questions", []),
            state.get("execution_target_ids"),
        )
    except ValueError as exc:
        logger.warning("execute_targets_rejected", error_type=type(exc).__name__)
        return {
            "execute_status": "failed",
            "workflow_error_code": "invalid_execution_targets",
            "terminal_reason": "Execution was stopped because its target set was invalid.",
            "terminal_status": "incomplete",
            "execution_target_ids": None,
        }
    t0 = time.monotonic()
    sq_total = len(sub_questions)

    tasks = [
        _execute_single(sq, i, sq_total, state, runtime)
        for i, sq in enumerate(sub_questions)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    sub_reports: list[SubReport] = []
    failed_queries: list[FailedQuery] = []

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                "execute_gather_exception",
                sub_question_id=sub_questions[i].id,
                error_type=type(result).__name__,
            )
            if not any(
                previous.sub_question_id == sub_questions[i].id
                for previous in state.get("sub_reports", [])
            ):
                sub_reports.append(SubReport(
                    sub_question_id=sub_questions[i].id,
                    question=sub_questions[i].question,
                    findings="This research step could not be completed.",
                    key_facts=["Execution failed"],
                    confidence=0.0,
                    gaps="Unexpected error during execution.",
                    sources=[],
                ))
            failed_queries.append({
                "sub_question_id": sub_questions[i].id,
                "query": sub_questions[i].search_queries,
                "error_code": "execution_error",
                "reason": "execution_error",
            })
        else:
            report, failure = result
            has_previous_report = any(
                previous.sub_question_id == sub_questions[i].id
                for previous in state.get("sub_reports", [])
            )
            if report and not (failure and has_previous_report):
                sub_reports.append(report)
            if failure:
                failed_queries.append(failure)

    logger.info(
        "execute_node_completed",
        total=len(sub_questions),
        successful=sum(1 for r in sub_reports if r.confidence > 0),
        failed=len(failed_queries),
        elapsed_s=round(time.monotonic() - t0, 2),
    )

    raw_corpus_version = state.get("corpus_version", 0)
    corpus_version = (
        raw_corpus_version
        if isinstance(raw_corpus_version, int)
        and not isinstance(raw_corpus_version, bool)
        and raw_corpus_version >= 0
        else 0
    )

    return {
        "sub_reports": merge_sub_reports(
            state.get("sub_reports"),
            sub_reports if sub_reports else None,
        ),
        "failed_queries": replace_failures_for_execution(
            state.get("failed_queries"),
            [sub_q.id for sub_q in sub_questions],
            failed_queries,
        ),
        "execution_target_ids": None,
        "execute_status": "completed",
        "repair_preparation_status": None,
        "workflow_error_code": None,
        "terminal_reason": None,
        "terminal_status": None,
        "corpus_version": corpus_version + 1,
    }


def route_after_execute(state: DeepResearchState) -> str:
    return "evaluate" if state.get("execute_status") == "completed" else "stop_incomplete"
