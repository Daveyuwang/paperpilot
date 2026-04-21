import json
import uuid
import structlog
import asyncio
from typing import Any
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.guest import require_guest_id
from app.api.sources import DiscoveredSource as DiscoveredSourceModel
from app.api.drafts import _resolve_llm

from app.deep_research.graph import compiled_graph
from app.deep_research.state import DeepResearchState
from app.deep_research.models import ResearchReport, ReportSection
from app.llm.client import LLMClient

logger = structlog.get_logger()
router = APIRouter()

# ── Request models (unchanged — frontend contract) ──────────────────────────

class DeepResearchInput(BaseModel):
    topic: str
    focus: str | None = None
    time_horizon: str = "broad"
    output_length: str = "medium"
    use_workspace_sources: bool = True
    discover_new_sources: bool = True
    must_include: str | None = None
    must_exclude: str | None = None
    notes: str | None = None
    target_deliverable_id: str | None = None
    depth: str = "standard"  # quick | standard | deep


class DRSourcePayload(BaseModel):
    id: str
    title: str
    authors: list[str] = []
    year: int | None = None
    abstract: str | None = None
    provider: str = ""
    paper_id: str | None = None
    label: str = "maybe"


class DRSectionPayload(BaseModel):
    id: str
    title: str
    content: str
    order: int
    linkedSourceIds: list[str] = []


class DeepResearchRequest(BaseModel):
    input: DeepResearchInput
    workspace_id: str
    workspace_sources: list[DRSourcePayload] = []
    existing_sections: list[DRSectionPayload] = []
    active_paper_id: str | None = None


# ── Response models (unchanged — frontend contract) ─────────────────────────

class ClarificationQuestion(BaseModel):
    field: str
    question: str
    suggestion: str | None = None


class DRSectionUpdate(BaseModel):
    section_index: int
    title: str
    mode: str
    generated_content: str
    source_ids_used: list[str]
    notes: str | None = None


class FollowUpItem(BaseModel):
    title: str
    description: str | None = None
    category: str | None = None
    priority: int = 50


class DeepResearchRunResult(BaseModel):
    run_id: str
    status: str
    clarification_questions: list[ClarificationQuestion] = []
    generated_title: str | None = None
    generated_outline: list[str] | None = None
    section_updates: list[DRSectionUpdate] = []
    discovered_sources: list[DiscoveredSourceModel] = []
    saved_source_ids: list[str] = []
    selected_source_ids: list[str] = []
    unresolved_questions: list[str] = []
    follow_up_items: list[FollowUpItem] = []
    summary: str | None = None
    message: str | None = None


# ── Validation (kept from original) ─────────────────────────────────────────

def _validate_and_clarify(req: DeepResearchRequest) -> list[ClarificationQuestion]:
    questions: list[ClarificationQuestion] = []
    topic = req.input.topic.strip()

    if not topic:
        questions.append(ClarificationQuestion(
            field="topic",
            question="What research topic or question would you like to investigate?",
        ))
        return questions

    words = topic.split()
    if len(words) <= 2 and not req.input.focus:
        questions.append(ClarificationQuestion(
            field="focus",
            question=f'"{topic}" is quite broad. What specific aspect or angle do you want to focus on?',
            suggestion=f"e.g. {topic} in the context of ..., or {topic} for ...",
        ))

    if not req.input.use_workspace_sources and not req.input.discover_new_sources:
        questions.append(ClarificationQuestion(
            field="sources",
            question="Both source options are disabled. Enable at least one.",
        ))

    return questions


async def _llm_validate_topic(req: DeepResearchRequest, client: LLMClient) -> list[ClarificationQuestion]:
    """Use LLM to evaluate if the topic is specific enough for research."""
    topic = req.input.topic.strip()
    focus = req.input.focus or ""
    output_length = req.input.output_length

    prompt = f"""Evaluate this research request for clarity and feasibility.

Topic: {topic}
Focus: {focus or "(none)"}
Output length: {output_length}
Has workspace sources: {req.input.use_workspace_sources}
Discover new sources: {req.input.discover_new_sources}

Decide if this is clear enough to research. Consider:
1. Is the topic specific enough for the requested output length?
2. Are there ambiguous terms that could mean very different things?
3. Is the scope manageable?

If the topic is clear enough, respond with: {{"ok": true}}
If clarification is needed, respond with: {{"ok": false, "questions": [{{"field": "topic_or_focus", "question": "your question", "suggestion": "optional suggestion"}}]}}

Respond ONLY with valid JSON, no other text."""

    try:
        content = await client.create_text(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.1,
        )
        text = content.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        if result.get("ok"):
            return []
        return [
            ClarificationQuestion(
                field=q.get("field", "topic"),
                question=q["question"],
                suggestion=q.get("suggestion"),
            )
            for q in result.get("questions", [])
        ]
    except Exception as exc:
        logger.warning("llm_validate_topic_failed", error=str(exc))
        return []


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_initial_state(req: DeepResearchRequest, api_key: str, base_url: str | None = None, model: str | None = None) -> DeepResearchState:
    topic = req.input.topic.strip()
    if req.input.focus:
        topic = f"{topic} — {req.input.focus.strip()}"
    if req.input.must_include:
        topic += f" (must include: {req.input.must_include.strip()})"

    user_sources = [s.title for s in req.workspace_sources if s.label != "discarded"]

    return DeepResearchState(
        topic=topic,
        user_sources=user_sources,
        depth=req.input.depth,
        workspace_id=req.workspace_id,
        api_key=api_key,
        llm_base_url=base_url,
        llm_model=model,
        sub_questions=[],
        sub_reports=[],
        failed_queries=[],
        replan_count=0,
        final_report=None,
    )


def _report_to_result(
    report: ResearchReport,
    run_id: str,
) -> DeepResearchRunResult:
    section_updates = [
        DRSectionUpdate(
            section_index=i,
            title=sec.heading,
            mode="fill_empty",
            generated_content=sec.content,
            source_ids_used=[],
        )
        for i, sec in enumerate(report.sections)
    ]

    source_urls = [s.url for s in report.sources if s.url]

    return DeepResearchRunResult(
        run_id=run_id,
        status="completed",
        generated_title=report.title,
        generated_outline=[sec.heading for sec in report.sections],
        section_updates=section_updates,
        unresolved_questions=[report.limitations] if report.limitations else [],
        follow_up_items=[
            FollowUpItem(title=f, description="Key finding from research", category="custom")
            for f in report.key_findings[:3]
        ],
        summary=report.executive_summary[:300],
        selected_source_ids=source_urls,
    )


# ── Main endpoint (batch) ───────────────────────────────────────────────────

@router.post("/run", response_model=DeepResearchRunResult)
async def run_deep_research(
    req: DeepResearchRequest,
    guest_id: str = Depends(require_guest_id),
):
    run_id = str(uuid.uuid4())[:8]

    clarifications = _validate_and_clarify(req)
    if clarifications:
        return DeepResearchRunResult(
            run_id=run_id, status="needs_clarification",
            clarification_questions=clarifications,
        )

    try:
        llm = await _resolve_llm(guest_id)
    except Exception as exc:
        logger.error("dr_llm_resolve_failed", error=str(exc))
        return DeepResearchRunResult(
            run_id=run_id, status="failed",
            message="Could not initialize LLM. Check your API key in Settings.",
        )

    # LLM-driven topic validation
    llm_clarifications = await _llm_validate_topic(req, llm)
    if llm_clarifications:
        return DeepResearchRunResult(
            run_id=run_id, status="needs_clarification",
            clarification_questions=llm_clarifications,
        )

    initial_state = _build_initial_state(req, llm.resolved.api_key, llm.resolved.base_url, llm.resolved.model)

    try:
        final_state = await compiled_graph.ainvoke(initial_state)
    except Exception as exc:
        logger.exception("dr_graph_failed", error=str(exc))
        return DeepResearchRunResult(
            run_id=run_id, status="failed",
            message=f"Research failed: {str(exc)[:200]}",
        )

    report = final_state.get("final_report")
    if not report:
        return DeepResearchRunResult(
            run_id=run_id, status="failed",
            message="Research completed but produced no report.",
        )

    # Detect all-failed: no sections and no sources means nothing worked
    if not report.sections and not report.sources:
        return DeepResearchRunResult(
            run_id=run_id, status="failed",
            message=(
                "All research sub-questions failed. "
                "Please check your Tavily API key and network connectivity."
            ),
        )

    return _report_to_result(report, run_id)


# ── Streaming endpoint ──────────────────────────────────────────────────────

@router.post("/run/stream")
async def run_deep_research_stream(
    req: DeepResearchRequest,
    guest_id: str = Depends(require_guest_id),
):
    async def event_stream():
        run_id = str(uuid.uuid4())[:8]

        def emit(event_type: str, data: dict) -> str:
            payload = json.dumps({"type": event_type, **data})
            return f"data: {payload}\n\n"

        try:
            # Validate
            yield emit("stage", {"stage": "validating", "message": "Validating input..."})
            await asyncio.sleep(0)

            clarifications = _validate_and_clarify(req)
            if clarifications:
                yield emit("result", {
                    "status": "needs_clarification", "run_id": run_id,
                    "clarification_questions": [c.model_dump() for c in clarifications],
                })
                return

            # Resolve LLM
            try:
                llm = await _resolve_llm(guest_id)
            except Exception as exc:
                logger.error("dr_stream_llm_resolve_failed", error=str(exc))
                yield emit("result", {
                    "status": "failed", "run_id": run_id,
                    "message": "Could not initialize LLM. Check your API key in Settings.",
                })
                return

            # LLM-driven topic validation (only for topics that passed fast checks)
            yield emit("stage", {"stage": "validating", "message": "Evaluating topic clarity..."})
            await asyncio.sleep(0)
            llm_clarifications = await _llm_validate_topic(req, llm)
            if llm_clarifications:
                yield emit("result", {
                    "status": "needs_clarification", "run_id": run_id,
                    "clarification_questions": [c.model_dump() for c in llm_clarifications],
                })
                return

            initial_state = _build_initial_state(req, llm.resolved.api_key, llm.resolved.base_url, llm.resolved.model)

            yield emit("stage", {"stage": "planning", "message": "Decomposing research topic..."})

            current_node = None
            final_report = None
            last_event_time = asyncio.get_event_loop().time()

            async for event in compiled_graph.astream_events(
                initial_state, version="v2"
            ):
                kind = event.get("event", "")
                name = event.get("name", "")
                data = event.get("data", {})

                now = asyncio.get_event_loop().time()

                # ── Node lifecycle events ──────────────────────────

                if kind == "on_chain_start" and name in (
                    "plan", "execute", "replan", "synthesize", "evaluate"
                ):
                    current_node = name
                    stage_map = {
                        "plan": ("planning", "Decomposing research topic..."),
                        "execute": ("executing", "Investigating sub-questions..."),
                        "evaluate": ("evaluating", "Evaluating research quality..."),
                        "replan": ("replanning", "Generating supplementary questions..."),
                        "synthesize": ("synthesizing", "Writing report..."),
                    }
                    stage, msg = stage_map.get(name, (name, f"Running {name}..."))
                    yield emit("stage", {"stage": stage, "message": msg})
                    last_event_time = now

                # ── Custom events from nodes (dispatch_custom_event) ──

                elif kind == "on_custom_event" and name == "execute_progress":
                    ep = data
                    evt = ep.get("event", "")
                    msg = ep.get("message", "")

                    if evt == "sq_start":
                        yield emit("activity", {
                            "activity_type": "thinking",
                            "label": msg,
                            "sq_index": ep.get("sq_index"),
                            "sq_total": ep.get("sq_total"),
                        })
                    elif evt == "searching":
                        yield emit("activity", {
                            "activity_type": "searching",
                            "label": msg,
                            "sq_index": ep.get("sq_index"),
                        })
                    elif evt == "reading":
                        yield emit("activity", {
                            "activity_type": "reading",
                            "label": msg,
                            "sq_index": ep.get("sq_index"),
                            "results_count": ep.get("results_count"),
                        })
                    elif evt == "summarizing":
                        yield emit("activity", {
                            "activity_type": "thinking",
                            "label": msg,
                            "sq_index": ep.get("sq_index"),
                        })
                    elif evt == "sq_complete":
                        yield emit("activity", {
                            "activity_type": "done",
                            "label": msg,
                            "sq_index": ep.get("sq_index"),
                            "confidence": ep.get("confidence"),
                        })
                        yield emit("progress", {
                            "sq_index": ep.get("sq_index"),
                            "sq_total": ep.get("sq_total"),
                            "confidence": ep.get("confidence"),
                            "question": ep.get("question"),
                            "duration_ms": ep.get("duration_ms"),
                            "error": ep.get("error"),
                        })
                    last_event_time = now

                elif kind == "on_custom_event" and name == "synthesize_progress":
                    sp = data
                    phase = sp.get("phase", "")
                    status = sp.get("status", "")
                    msg = sp.get("message", "")

                    if phase == "outline" and status == "start":
                        yield emit("activity", {
                            "activity_type": "thinking",
                            "label": msg,
                        })
                    elif phase == "outline" and status == "done":
                        headings = sp.get("section_headings", [])
                        yield emit("activity", {
                            "activity_type": "done",
                            "label": msg,
                        })
                        yield emit("synthesize_outline", {
                            "title": sp.get("title", ""),
                            "section_headings": headings,
                        })
                    elif phase == "section" and status == "start":
                        yield emit("activity", {
                            "activity_type": "writing",
                            "label": msg,
                            "section_index": sp.get("section_index"),
                            "section_total": sp.get("section_total"),
                            "section_title": sp.get("section_title"),
                        })
                        yield emit("synthesize_section", {
                            "status": "writing",
                            "section_index": sp.get("section_index"),
                            "section_total": sp.get("section_total"),
                            "section_title": sp.get("section_title"),
                        })
                    elif phase == "section" and status in ("done", "failed"):
                        yield emit("activity", {
                            "activity_type": "done" if status == "done" else "error",
                            "label": msg,
                            "section_index": sp.get("section_index"),
                            "duration_ms": sp.get("duration_ms"),
                        })
                        yield emit("synthesize_section", {
                            "status": status,
                            "section_index": sp.get("section_index"),
                            "section_total": sp.get("section_total"),
                            "section_title": sp.get("section_title"),
                            "duration_ms": sp.get("duration_ms"),
                        })
                    last_event_time = now

                # ── Tool events (legacy search events) ──

                elif kind == "on_tool_start":
                    tool_input = data.get("input", {})
                    query = ""
                    if isinstance(tool_input, dict):
                        query = tool_input.get("query", "") or tool_input.get("q", "")
                    if query:
                        yield emit("activity", {"activity_type": "searching", "label": f"Searching: {str(query)[:60]}"})
                    last_event_time = now

                # ── Node completion events ──

                elif kind == "on_chain_end" and name == "plan":
                    output = data.get("output", {})
                    sub_qs = output.get("sub_questions", [])
                    if sub_qs:
                        yield emit("progress", {
                            "stage": "planning",
                            "sub_questions": [
                                {"id": sq.id, "question": sq.question}
                                for sq in sub_qs
                            ],
                            "message": f"Generated {len(sub_qs)} sub-questions",
                        })
                    last_event_time = now

                elif kind == "on_chain_end" and name == "execute":
                    output = data.get("output", {})
                    reports = output.get("sub_reports", [])
                    if reports:
                        yield emit("progress", {
                            "stage": "executing",
                            "sub_reports_summary": [
                                {
                                    "sub_question_id": r.sub_question_id,
                                    "confidence": r.confidence,
                                    "question": r.question,
                                }
                                for r in reports
                            ],
                            "message": f"Completed {len(reports)} sub-investigations",
                        })
                    last_event_time = now

                elif kind == "on_chain_end" and name == "replan":
                    output = data.get("output", {})
                    new_qs = output.get("sub_questions", [])
                    if new_qs:
                        yield emit("progress", {
                            "stage": "replanning",
                            "supplementary_questions": [
                                {"id": sq.id, "question": sq.question}
                                for sq in new_qs
                            ],
                            "message": f"Added {len(new_qs)} supplementary questions",
                        })
                    last_event_time = now

                elif kind == "on_chain_end" and name == "synthesize":
                    output = data.get("output", {})
                    final_report = output.get("final_report")
                    last_event_time = now

            if final_report:
                # Detect all-failed: no sections and no sources means nothing worked
                is_empty = not final_report.sections and not final_report.sources
                if is_empty:
                    yield emit("result", {
                        "status": "failed", "run_id": run_id,
                        "message": (
                            "All research sub-questions failed. "
                            "This usually means the search service is unavailable "
                            "or the API key is not configured. "
                            "Please check your Tavily API key and network connectivity."
                        ),
                    })
                else:
                    result = _report_to_result(final_report, run_id)
                    yield emit("tailored_outline", {
                        "generated_title": result.generated_title,
                        "sections": result.generated_outline or [],
                    })
                    yield emit("sections_outline", {
                        "titles": result.generated_outline or [],
                    })
                    for update in result.section_updates:
                        yield emit("section_complete", {
                            "index": update.section_index,
                            "title": update.title,
                            "preview": update.generated_content[:200] + ("..." if len(update.generated_content) > 200 else ""),
                            "source_count": len(update.source_ids_used),
                            "skipped": False,
                        })

                    yield emit("result", {
                        "status": "completed", "run_id": run_id,
                        "data": {
                            "section_updates": [u.model_dump() for u in result.section_updates],
                            "discovered_sources": [],
                            "saved_source_ids": [],
                            "selected_source_ids": result.selected_source_ids,
                            "unresolved_questions": result.unresolved_questions,
                            "follow_up_items": [f.model_dump() for f in result.follow_up_items],
                            "summary": result.summary,
                            "generated_title": result.generated_title,
                            "generated_outline": result.generated_outline,
                        },
                    })
            else:
                yield emit("result", {
                    "status": "failed", "run_id": run_id,
                    "message": "Research completed but produced no report.",
                })

        except Exception as exc:
            logger.exception("dr_stream_unexpected_error", error=str(exc))
            yield emit("result", {
                "status": "failed", "run_id": run_id,
                "message": f"Unexpected error during research run: {str(exc)[:200]}",
            })

    return StreamingResponse(event_stream(), media_type="text/event-stream")
