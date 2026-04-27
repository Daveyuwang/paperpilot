from __future__ import annotations

import asyncio
import time
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from langchain_core.callbacks import adispatch_custom_event
from pydantic import BaseModel, Field

from app.deep_research.llm_factory import make_llm
from app.deep_research.models import ResearchReport, ReportSection, SourceRef
from app.deep_research.prompts import SYNTHESIZE_USER
from app.deep_research.state import DeepResearchState

logger = structlog.get_logger()

SECTION_TIMEOUT_S = 90
OUTLINE_TIMEOUT_S = 90

OUTLINE_SYSTEM = """\
You are a research synthesis expert. Given sub-reports on a research topic, \
design the structure of a comprehensive report.

Rules:
- The title MUST be a proper academic research article title — specific, \
descriptive, and scholarly (10-20 words). Do NOT simply echo or paraphrase \
the user's topic. Good: "Multi-Agent Cooperative Perception: Architectures, \
Communication Protocols, and Safety Guarantees in Autonomous Driving". \
Bad: "Research Report: autonomous driving multi-agent systems".
- Synthesize across sub-reports — don't just list them.
- Section headings should represent logical themes, not mirror sub-questions.
- Use academic but accessible tone.
- Executive summary should cover all major findings in 2-3 paragraphs.
- Key findings should be 5-10 concise bullet point strings."""

SECTION_SYSTEM = """\
You are writing one section of a research report.
Write focused, well-structured content for the section titled "{heading}".

Rules:
- Use ONLY evidence from the provided sub-reports. Do not invent claims.
- Write 2-4 paragraphs with markdown formatting.
- Synthesize findings across multiple sub-reports where relevant.
- Be specific and cite sources by name when possible.
- Output ONLY the section content. No heading/title prefix."""

SECTION_USER = """\
Report topic: {topic}
Section to write: "{heading}"

Full report outline:
{outline}

Relevant sub-reports:
{sub_reports_block}

Write the content for this section."""


class ReportOutline(BaseModel):
    title: str = Field(description=(
        "A concise, academic-style research article title (10-20 words). "
        "Must be specific to the research findings, not a generic restatement of the topic. "
        "Example style: 'Comparative Analysis of Attention Mechanisms for Long-Context Language Understanding'"
    ))
    executive_summary: str = Field(description="2-3 paragraph executive summary")
    section_headings: list[str] = Field(description="3-6 section headings")
    key_findings: list[str] = Field(description="5-10 key finding bullet points")
    limitations: str = Field(description="Limitations paragraph")


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def _invoke_outline(structured_llm, messages):
    return await asyncio.wait_for(
        structured_llm.ainvoke(messages),
        timeout=OUTLINE_TIMEOUT_S,
    )


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def _invoke_section(llm, messages):
    return await asyncio.wait_for(
        llm.ainvoke(messages),
        timeout=SECTION_TIMEOUT_S,
    )


def _build_sub_reports_block(sub_reports):
    return "\n\n===\n\n".join(
        f"Sub-question: {r.question}\n"
        f"Confidence: {r.confidence}\n"
        f"Findings:\n{r.findings}\n"
        f"Key facts:\n" + "\n".join(f"- {f}" for f in r.key_facts) + "\n"
        f"Gaps: {r.gaps}\n"
        f"Sources: " + ", ".join(s.title for s in r.sources if s.title)
        for r in sub_reports
        if r.findings and r.confidence > 0
    )


async def synthesize_node(state: DeepResearchState) -> dict:
    topic = state["topic"]
    sub_reports = state.get("sub_reports", [])
    t0 = time.monotonic()

    sub_reports_block = _build_sub_reports_block(sub_reports)

    if not sub_reports_block:
        return {
            "final_report": ResearchReport(
                title="Preliminary Research Findings: Insufficient Data for Comprehensive Analysis",
                executive_summary="Unable to produce a meaningful report — all sub-investigations returned insufficient data.",
                sections=[],
                key_findings=["Insufficient data to draw conclusions"],
                limitations="All research sub-questions failed or returned no usable results.",
                sources=[],
            )
        }

    # Phase 1: Generate outline + executive summary
    await adispatch_custom_event("synthesize_progress", {
        "phase": "outline", "status": "start",
        "message": "Designing report structure...",
    })

    llm = make_llm(state, max_tokens=3000, temperature=0.3)
    outline_llm = llm.with_structured_output(ReportOutline)

    try:
        outline: ReportOutline = await _invoke_outline(
            outline_llm,
            [
                {"role": "system", "content": OUTLINE_SYSTEM},
                {"role": "user", "content": SYNTHESIZE_USER.format(
                    topic=topic, sub_reports_block=sub_reports_block,
                )},
            ],
        )
    except Exception as exc:
        logger.error("synthesize_outline_failed", error=repr(exc))
        err_desc = repr(exc) if not str(exc) else str(exc)[:200]
        return {
            "final_report": ResearchReport(
                title="Partial Research Synthesis: Report Generation Incomplete",
                executive_summary=f"Report outline generation failed: {err_desc}",
                sections=[],
                key_findings=[f.key_facts[0] if f.key_facts else f.question
                              for f in sub_reports[:5] if f.findings and f.confidence > 0],
                limitations="Outline generation failed. Sub-report data is available.",
                sources=[],
            )
        }

    if (
        outline.title.lower().startswith("research report:")
        or outline.title.strip().lower() == topic.strip().lower()
        or len(outline.title.split()) < 4
    ):
        outline.title = f"A Comprehensive Analysis of {topic}"

    await adispatch_custom_event("synthesize_progress", {
        "phase": "outline", "status": "done",
        "message": f"Report outline ready: {len(outline.section_headings)} sections",
        "title": outline.title,
        "section_headings": outline.section_headings,
    })

    logger.info(
        "synthesize_outline_done",
        title=outline.title,
        num_sections=len(outline.section_headings),
        elapsed_s=round(time.monotonic() - t0, 2),
    )

    # Phase 2: Generate each section independently
    outline_text = "\n".join(f"- {h}" for h in outline.section_headings)
    sections: list[ReportSection] = []
    section_llm = make_llm(state, max_tokens=1500, temperature=0.3)
    total_sections = len(outline.section_headings)

    for i, heading in enumerate(outline.section_headings):
        t_sec = time.monotonic()

        await adispatch_custom_event("synthesize_progress", {
            "phase": "section", "status": "start",
            "section_index": i, "section_total": total_sections,
            "section_title": heading,
            "message": f"Writing: {heading}",
        })

        try:
            result = await _invoke_section(
                section_llm,
                [
                    {"role": "system", "content": SECTION_SYSTEM.format(heading=heading)},
                    {"role": "user", "content": SECTION_USER.format(
                        topic=topic,
                        heading=heading,
                        outline=outline_text,
                        sub_reports_block=sub_reports_block,
                    )},
                ],
            )
            content = result.content if hasattr(result, "content") else str(result)
            sections.append(ReportSection(heading=heading, content=content.strip()))

            await adispatch_custom_event("synthesize_progress", {
                "phase": "section", "status": "done",
                "section_index": i, "section_total": total_sections,
                "section_title": heading,
                "message": f"Completed: {heading}",
                "duration_ms": round((time.monotonic() - t_sec) * 1000),
            })
        except Exception as exc:
            logger.warning("synthesize_section_failed", index=i, heading=heading, error=str(exc))
            sections.append(ReportSection(
                heading=heading,
                content=f"*Section generation failed: {str(exc)[:100]}*",
            ))
            await adispatch_custom_event("synthesize_progress", {
                "phase": "section", "status": "failed",
                "section_index": i, "section_total": total_sections,
                "section_title": heading,
                "message": f"Failed: {heading}",
            })

    # Deduplicate sources
    seen_urls: set[str] = set()
    deduped_sources: list[SourceRef] = []
    for sr in sub_reports:
        for src in sr.sources:
            if src.url and src.url not in seen_urls:
                seen_urls.add(src.url)
                deduped_sources.append(src)

    report = ResearchReport(
        title=outline.title,
        executive_summary=outline.executive_summary,
        sections=sections,
        key_findings=outline.key_findings,
        limitations=outline.limitations,
        sources=deduped_sources,
    )

    logger.info(
        "synthesize_node_completed",
        title=report.title,
        num_sections=len(report.sections),
        num_sources=len(report.sources),
        elapsed_s=round(time.monotonic() - t0, 2),
    )
    return {"final_report": report}
