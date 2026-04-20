from __future__ import annotations

import time
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.deep_research.llm_factory import make_llm
from app.deep_research.models import ResearchReport, SourceRef
from app.deep_research.prompts import SYNTHESIZE_SYSTEM, SYNTHESIZE_USER
from app.deep_research.state import DeepResearchState

logger = structlog.get_logger()


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def _invoke_synthesize(structured_llm, messages):
    return await structured_llm.ainvoke(messages)


async def synthesize_node(state: DeepResearchState) -> dict:
    topic = state["topic"]
    sub_reports = state.get("sub_reports", [])
    t0 = time.monotonic()

    sub_reports_block = "\n\n===\n\n".join(
        f"Sub-question: {r.question}\n"
        f"Confidence: {r.confidence}\n"
        f"Findings:\n{r.findings}\n"
        f"Key facts:\n" + "\n".join(f"- {f}" for f in r.key_facts) + "\n"
        f"Gaps: {r.gaps}\n"
        f"Sources: " + ", ".join(s.title for s in r.sources if s.title)
        for r in sub_reports
        if r.findings and r.confidence > 0
    )

    if not sub_reports_block:
        return {
            "final_report": ResearchReport(
                title=f"Research Report: {topic}",
                executive_summary="Unable to produce a meaningful report — all sub-investigations returned insufficient data.",
                sections=[],
                key_findings=["Insufficient data to draw conclusions"],
                limitations="All research sub-questions failed or returned no usable results.",
                sources=[],
            )
        }

    user_msg = SYNTHESIZE_USER.format(
        topic=topic,
        sub_reports_block=sub_reports_block,
    )

    llm = make_llm(state, max_tokens=8000, temperature=0.3)
    structured_llm = llm.with_structured_output(ResearchReport)

    try:
        report: ResearchReport = await _invoke_synthesize(
            structured_llm,
            [
                {"role": "system", "content": SYNTHESIZE_SYSTEM},
                {"role": "user", "content": user_msg},
            ]
        )

        seen_urls: set[str] = set()
        deduped_sources: list[SourceRef] = []
        for sr in sub_reports:
            for src in sr.sources:
                if src.url and src.url not in seen_urls:
                    seen_urls.add(src.url)
                    deduped_sources.append(src)
        report.sources = deduped_sources

        logger.info(
            "synthesize_node_completed",
            title=report.title,
            num_sections=len(report.sections),
            num_sources=len(report.sources),
            elapsed_s=round(time.monotonic() - t0, 2),
        )
        return {"final_report": report}

    except Exception as exc:
        logger.error("synthesize_node_failed", error=str(exc))
        raise
