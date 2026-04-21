from __future__ import annotations

import asyncio
import time
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from langchain_core.callbacks import adispatch_custom_event

from app.deep_research.config import FETCH_TOP_N
from app.deep_research.llm_factory import make_llm
from app.deep_research.models import SubQuestion, SubReport, SourceRef
from app.deep_research.prompts import EXECUTE_SYSTEM, EXECUTE_USER
from app.deep_research.state import DeepResearchState
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
) -> tuple[SubReport | None, dict | None]:
    try:
        t_sq = time.monotonic()

        await adispatch_custom_event("execute_progress", {
            "event": "sq_start",
            "sq_index": sq_index, "sq_total": sq_total,
            "question": sub_q.question,
            "message": f"Investigating: {sub_q.question[:80]}",
        })

        await adispatch_custom_event("execute_progress", {
            "event": "searching",
            "sq_index": sq_index, "sq_total": sq_total,
            "queries": sub_q.search_queries[:3],
            "message": f"Searching: {sub_q.search_queries[0][:60]}",
        })

        search_results = await tavily_search(sub_q.search_queries)

        if not search_results:
            await adispatch_custom_event("execute_progress", {
                "event": "sq_complete",
                "sq_index": sq_index, "sq_total": sq_total,
                "question": sub_q.question,
                "confidence": 0.0,
                "error": "no_results",
                "duration_ms": round((time.monotonic() - t_sq) * 1000),
                "message": f"No results: {sub_q.question[:60]}",
            })
            return SubReport(
                sub_question_id=sub_q.id,
                question=sub_q.question,
                findings="No search results were found for this sub-question.",
                key_facts=["No data available"],
                confidence=0.0,
                gaps="Complete lack of search results; unable to investigate this question.",
                sources=[],
            ), {"query": sub_q.search_queries, "reason": "no_results"}

        await adispatch_custom_event("execute_progress", {
            "event": "reading",
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
            title = result.get("title", "")
            snippet = result.get("snippet", "")

            page_content = ""
            for furl, fcontent in fetched:
                if furl == url and fcontent:
                    page_content = fcontent
                    break

            block = f"### {title}\nURL: {url}\n"
            if page_content:
                block += f"Content:\n{page_content}\n"
            elif snippet:
                block += f"Snippet:\n{snippet}\n"
            context_parts.append(block)
            source_refs.append(SourceRef(url=url, title=title))

        for result in search_results[FETCH_TOP_N:]:
            snippet = result.get("snippet", "")
            if snippet:
                context_parts.append(
                    f"### {result.get('title', '')}\nURL: {result['url']}\nSnippet: {snippet}\n"
                )
                source_refs.append(SourceRef(url=result["url"], title=result.get("title", "")))

        search_context = "\n---\n".join(context_parts)

        await adispatch_custom_event("execute_progress", {
            "event": "summarizing",
            "sq_index": sq_index, "sq_total": sq_total,
            "message": f"Analyzing findings for: {sub_q.question[:60]}",
        })

        user_msg = EXECUTE_USER.format(
            question=sub_q.question,
            search_context=search_context,
        )

        llm = make_llm(state, max_tokens=3000, temperature=0.2)
        structured_llm = llm.with_structured_output(SubReport)

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
            error=str(exc),
        )
        await adispatch_custom_event("execute_progress", {
            "event": "sq_complete",
            "sq_index": sq_index, "sq_total": sq_total,
            "question": sub_q.question,
            "confidence": 0.0,
            "error": str(exc)[:100],
            "duration_ms": round((time.monotonic() - t_sq) * 1000),
            "message": f"Failed: {sub_q.question[:60]}",
        })
        fallback = SubReport(
            sub_question_id=sub_q.id,
            question=sub_q.question,
            findings=f"Failed to investigate: {str(exc)[:200]}",
            key_facts=["Investigation failed"],
            confidence=0.0,
            gaps="Complete failure to investigate this sub-question.",
            sources=[],
        )
        return fallback, {"query": sub_q.search_queries, "reason": str(exc)[:200]}


async def execute_node(state: DeepResearchState) -> dict:
    sub_questions = state["sub_questions"]
    t0 = time.monotonic()
    sq_total = len(sub_questions)

    tasks = [_execute_single(sq, i, sq_total, state) for i, sq in enumerate(sub_questions)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    sub_reports: list[SubReport] = []
    failed_queries: list[dict] = []

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                "execute_gather_exception",
                sub_question_id=sub_questions[i].id,
                error=str(result),
            )
            sub_reports.append(SubReport(
                sub_question_id=sub_questions[i].id,
                question=sub_questions[i].question,
                findings=f"Execution error: {str(result)[:200]}",
                key_facts=["Execution failed"],
                confidence=0.0,
                gaps="Unexpected error during execution.",
                sources=[],
            ))
            failed_queries.append({
                "query": sub_questions[i].search_queries,
                "reason": str(result)[:200],
            })
        else:
            report, failure = result
            if report:
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

    return {
        "sub_reports": sub_reports,
        "failed_queries": state.get("failed_queries", []) + failed_queries,
    }
