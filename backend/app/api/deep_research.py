import json
import uuid
import structlog
import httpx
import asyncio
from typing import Any
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.guest import require_guest_id
from app.api.sources import (
    _parse_openalex, _parse_arxiv, _dedupe, _normalize_title,
    DiscoveredSource as DiscoveredSourceModel,
    OPENALEX_API, ARXIV_API,
)
from app.api.drafts import _resolve_llm, _build_source_context, SourcePayload

logger = structlog.get_logger()
router = APIRouter()

# ── Request models ────────────────────────────────────────────────────────

class DeepResearchInput(BaseModel):
    topic: str
    focus: str | None = None
    time_horizon: str = "broad"  # recent_2y | recent_5y | broad
    output_length: str = "medium"  # short | medium
    use_workspace_sources: bool = True
    discover_new_sources: bool = True
    must_include: str | None = None
    must_exclude: str | None = None
    notes: str | None = None
    target_deliverable_id: str | None = None


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


# ── Response models ───────────────────────────────────────────────────────

class ClarificationQuestion(BaseModel):
    field: str
    question: str
    suggestion: str | None = None


class DRSectionUpdate(BaseModel):
    section_index: int
    title: str
    mode: str  # fill_empty | preview_replace
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
    status: str  # completed | needs_clarification | failed | blocked
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


# ── Deep research section templates ───────────────────────────────────────

DR_SECTIONS = [
    "Problem Framing",
    "Current Landscape",
    "Key Approaches and Tradeoffs",
    "Open Questions / Next Directions",
]

SECTION_INTENT = {
    "Problem Framing": (
        "Define the research question or problem clearly. "
        "State why it matters and bound the scope. "
        "Be specific about what this research addresses and what it does not."
    ),
    "Current Landscape": (
        "Summarize the major strands of existing work. "
        "Cluster approaches where possible rather than listing papers. "
        "Highlight the dominant paradigms and where the field currently stands."
    ),
    "Key Approaches and Tradeoffs": (
        "Compare the main approaches head-to-head. "
        "Emphasize differences in assumptions, strengths, and limitations. "
        "Be concrete about what each approach trades off."
    ),
    "Open Questions / Next Directions": (
        "Synthesize what remains unresolved based on the evidence. "
        "Identify the most promising directions and areas of uncertainty. "
        "Be honest about evidence gaps."
    ),
}

# ── Tailored outline generation ───────────────────────────────────────────

async def _generate_tailored_outline(
    llm: Any,
    inp: "DeepResearchInput",
    sources: list["SourcePayload"],
) -> tuple[str, list[str]]:
    """Stage 1: Generate a document title and tailored section outline.

    Uses the base DR_SECTIONS as a scaffold but allows the LLM to rename,
    add (up to 2 extra), or drop sections based on the topic and sources.
    Returns (generated_title, list_of_section_titles).
    """
    source_summaries = []
    for s in sources[:6]:
        line = s.title
        if s.year:
            line += f" ({s.year})"
        if s.abstract:
            line += f" — {s.abstract[:120]}"
        source_summaries.append(line)
    source_block = "\n".join(f"- {s}" for s in source_summaries) if source_summaries else "(no sources yet)"

    base_sections_str = "\n".join(f"- {s}" for s in DR_SECTIONS)

    system = (
        "You are a research document architect. Given a research topic and available sources, "
        "produce a tailored document title and section outline for a deep research brief.\n\n"
        "Rules:\n"
        "- Start from the base section template below. You may rename sections, drop irrelevant ones, "
        "or add up to 2 new sections — but keep the total between 3 and 6 sections.\n"
        "- The title should be specific to the topic, not generic (e.g. 'Retrieval-Augmented Generation for Scientific QA' not 'Deep Research Brief').\n"
        "- Section titles should be concise (2-5 words each).\n"
        "- Return valid JSON: {\"title\": \"...\", \"sections\": [\"...\", ...]}\n"
        "- Do NOT include section content, only titles."
    )
    user_msg = (
        f"Research topic: {inp.topic}\n"
        + (f"Focus: {inp.focus}\n" if inp.focus else "")
        + (f"Must include: {inp.must_include}\n" if inp.must_include else "")
        + f"\nBase section template:\n{base_sections_str}\n"
        f"\nAvailable sources:\n{source_block}"
    )

    try:
        result = await llm.create_json(
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=400,
            temperature=0.3,
        )
        if isinstance(result, dict):
            title = result.get("title", "Deep Research Brief")
            sections = result.get("sections", DR_SECTIONS[:])
            if not isinstance(sections, list) or len(sections) < 3:
                sections = DR_SECTIONS[:]
            sections = [str(s) for s in sections[:6]]
            return str(title), sections
    except Exception as exc:
        logger.warning("dr_tailored_outline_failed", error=str(exc))

    return "Deep Research Brief", DR_SECTIONS[:]


# ── Pipeline helpers ───────────────────────────────────────────────────────

LABEL_PRIORITY = {"core": 0, "background": 1, "general": 2, "": 3}


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
            question="Both source options are disabled. Enable at least one: use saved workspace sources, discover new sources, or both.",
        ))

    if req.input.target_deliverable_id and not req.existing_sections:
        questions.append(ClarificationQuestion(
            field="deliverable",
            question="The selected deliverable has no sections. Create a new deliverable instead?",
        ))

    return questions


def _prepare_queries(inp: DeepResearchInput) -> list[str]:
    topic = inp.topic.strip()
    queries = [topic]

    if inp.focus and inp.focus.strip():
        queries.append(f"{topic} {inp.focus.strip()}")

    if inp.must_include and inp.must_include.strip():
        q3 = f"{topic} {inp.must_include.strip()}"
        if q3 not in queries:
            queries.append(q3)

    return queries[:3]


async def _discover_all(queries: list[str]) -> list[DiscoveredSourceModel]:
    all_results: list[DiscoveredSourceModel] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for q in queries:
            try:
                resp = await client.get(OPENALEX_API, params={
                    "search": q, "per_page": 10,
                    "sort": "relevance_score:desc",
                    "select": "id,title,authorships,publication_year,doi,abstract_inverted_index,cited_by_count,primary_location",
                })
                if resp.status_code == 200:
                    all_results.extend(_parse_openalex(resp.json().get("results", [])))
            except Exception as exc:
                logger.warning("dr_openalex_failed", query=q, error=str(exc))

            try:
                resp = await client.get(ARXIV_API, params={
                    "search_query": f"all:{q}", "start": 0,
                    "max_results": 8, "sortBy": "relevance", "sortOrder": "descending",
                })
                if resp.status_code == 200:
                    all_results.extend(_parse_arxiv(resp.text))
            except Exception as exc:
                logger.warning("dr_arxiv_failed", query=q, error=str(exc))

    return _dedupe(all_results)


def _dedupe_and_select(
    workspace_sources: list[DRSourcePayload],
    discovered: list[DiscoveredSourceModel],
    use_workspace: bool,
) -> tuple[list[SourcePayload], list[DiscoveredSourceModel], list[str], list[str]]:
    """Returns (selected_for_drafting, discovered_to_save, saved_ids, selected_ids)."""
    seen_titles: set[str] = set()
    seen_doi: set[str] = set()
    seen_arxiv: set[str] = set()

    selected: list[SourcePayload] = []
    selected_ids: list[str] = []

    if use_workspace:
        ws_sorted = sorted(
            [s for s in workspace_sources if s.label != "discarded"],
            key=lambda s: LABEL_PRIORITY.get(s.label, 3),
        )
        for s in ws_sorted:
            if len(selected) >= 8:
                break
            norm = _normalize_title(s.title)
            seen_titles.add(norm)
            selected.append(SourcePayload(
                id=s.id, title=s.title, authors=s.authors,
                year=s.year, abstract=s.abstract, provider=s.provider,
                paper_id=s.paper_id,
            ))
            selected_ids.append(s.id)

    saved_to_add: list[DiscoveredSourceModel] = []
    saved_ids: list[str] = []

    for d in discovered:
        if len(selected) >= 8:
            break
        norm = _normalize_title(d.title)
        if norm in seen_titles:
            continue
        if d.doi and d.doi.lower() in seen_doi:
            continue
        if d.arxiv_id and d.arxiv_id.lower() in seen_arxiv:
            continue

        seen_titles.add(norm)
        if d.doi:
            seen_doi.add(d.doi.lower())
        if d.arxiv_id:
            seen_arxiv.add(d.arxiv_id.lower())

        temp_id = f"dr-{d.provider}-{uuid.uuid4().hex[:6]}"
        selected.append(SourcePayload(
            id=temp_id, title=d.title, authors=d.authors,
            year=d.year, abstract=d.abstract, provider=d.provider,
            paper_id=None,
        ))
        selected_ids.append(temp_id)
        saved_to_add.append(d)
        saved_ids.append(temp_id)

    return selected, saved_to_add, saved_ids, selected_ids


async def _draft_single_section(
    llm: Any,
    inp: DeepResearchInput,
    idx: int,
    title: str,
    all_sections: list[tuple[int, str, str]],
    sources: list[SourcePayload],
) -> DRSectionUpdate:
    """Draft a single section. Used by both batch and streaming paths."""
    source_ctx, used_ids = _build_source_context(sources)
    style = "synthesis-heavy, comparative, concise academic analysis"
    length_hint = "2-3 focused paragraphs" if inp.output_length == "short" else "3-5 focused paragraphs"
    outline = "\n".join(f"{i+1}. {t}" for i, (_, t, _) in enumerate(all_sections))

    intent = SECTION_INTENT.get(title, "Write a focused analysis for this section.")
    system = (
        f"You are a research writing assistant producing a deep research brief.\n"
        f"Style: {style}\n"
        f"Section purpose: {intent}\n"
        f"Rules:\n"
        f"- Use ONLY the provided source context. Do not invent claims.\n"
        f"- Be concise and specific. Avoid generic filler.\n"
        f"- Stay conservative when source support is weak.\n"
        f"- Write {length_hint}.\n"
        f"- Output ONLY the section content text. No markdown headers, no section title."
    )
    user_msg = (
        f"Research topic: {inp.topic}\n"
        + (f"Focus: {inp.focus}\n" if inp.focus else "")
        + (f"Must include: {inp.must_include}\n" if inp.must_include else "")
        + (f"Must exclude: {inp.must_exclude}\n" if inp.must_exclude else "")
        + f"\nDocument outline:\n{outline}\n\n"
        f"Write content for section: \"{title}\"\n\n"
        f"Source context:\n{source_ctx}"
    )

    try:
        content = await llm.create_text(
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=1500 if inp.output_length == "medium" else 900,
            temperature=0.3,
        )
        return DRSectionUpdate(
            section_index=idx, title=title, mode="fill_empty",
            generated_content=content.strip(), source_ids_used=used_ids,
        )
    except Exception as exc:
        logger.warning("dr_draft_section_failed", section=title, error=str(exc))
        return DRSectionUpdate(
            section_index=idx, title=title, mode="fill_empty",
            generated_content="", source_ids_used=[],
            notes=f"Generation failed: {str(exc)[:100]}",
        )


async def _draft_all_sections(
    llm: Any,
    inp: DeepResearchInput,
    sections: list[tuple[int, str, str]],
    sources: list[SourcePayload],
) -> list[DRSectionUpdate]:
    source_ctx, used_ids = _build_source_context(sources)
    style = "synthesis-heavy, comparative, concise academic analysis"
    length_hint = "2-3 focused paragraphs" if inp.output_length == "short" else "3-5 focused paragraphs"
    outline = "\n".join(f"{i+1}. {title}" for i, (_, title, _) in enumerate(sections))

    updates: list[DRSectionUpdate] = []
    for idx, title, existing_content in sections:
        if existing_content.strip():
            updates.append(DRSectionUpdate(
                section_index=idx, title=title, mode="preview_replace",
                generated_content="", source_ids_used=[], notes="Section has content; skipped auto-draft.",
            ))
            continue

        intent = SECTION_INTENT.get(title, "Write a focused analysis for this section.")
        system = (
            f"You are a research writing assistant producing a deep research brief.\n"
            f"Style: {style}\n"
            f"Section purpose: {intent}\n"
            f"Rules:\n"
            f"- Use ONLY the provided source context. Do not invent claims.\n"
            f"- Be concise and specific. Avoid generic filler.\n"
            f"- Stay conservative when source support is weak.\n"
            f"- Write {length_hint}.\n"
            f"- Output ONLY the section content text. No markdown headers, no section title."
        )
        user_msg = (
            f"Research topic: {inp.topic}\n"
            + (f"Focus: {inp.focus}\n" if inp.focus else "")
            + (f"Must include: {inp.must_include}\n" if inp.must_include else "")
            + (f"Must exclude: {inp.must_exclude}\n" if inp.must_exclude else "")
            + f"\nDocument outline:\n{outline}\n\n"
            f"Write content for section: \"{title}\"\n\n"
            f"Source context:\n{source_ctx}"
        )

        try:
            content = await llm.create_text(
                system=system,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=1500 if inp.output_length == "medium" else 900,
                temperature=0.3,
            )
            updates.append(DRSectionUpdate(
                section_index=idx, title=title, mode="fill_empty",
                generated_content=content.strip(), source_ids_used=used_ids,
            ))
        except Exception as exc:
            logger.warning("dr_draft_section_failed", section=title, error=str(exc))
            updates.append(DRSectionUpdate(
                section_index=idx, title=title, mode="fill_empty",
                generated_content="", source_ids_used=[],
                notes=f"Generation failed: {str(exc)[:100]}",
            ))

    return updates


async def _extract_unresolved(llm: Any, updates: list[DRSectionUpdate], topic: str) -> list[str]:
    drafted_text = "\n\n".join(
        f"## {u.title}\n{u.generated_content}"
        for u in updates if u.generated_content.strip()
    )
    if not drafted_text.strip():
        return []

    system = (
        "You are a research analyst. Given a drafted deep research brief, "
        "extract 1-5 specific unresolved questions or evidence gaps. "
        "Return a JSON array of strings. Each string should be a concrete, "
        "actionable research question. Do not repeat what was already answered."
    )
    user_msg = f"Research topic: {topic}\n\nDrafted content:\n{drafted_text}"

    try:
        result = await llm.create_json(
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=500,
            temperature=0.2,
        )
        if isinstance(result, list):
            return [str(q) for q in result[:5]]
        return []
    except Exception as exc:
        logger.warning("dr_extract_unresolved_failed", error=str(exc))
        return []


def _generate_follow_ups(unresolved: list[str]) -> list[FollowUpItem]:
    items: list[FollowUpItem] = []
    for i, q in enumerate(unresolved[:3]):
        cat = "approach" if any(w in q.lower() for w in ["method", "approach", "technique", "algorithm"]) \
            else "experiments" if any(w in q.lower() for w in ["evaluat", "benchmark", "dataset", "experiment"]) \
            else "motivation" if any(w in q.lower() for w in ["why", "motivation", "impact", "importance"]) \
            else "custom"
        items.append(FollowUpItem(
            title=q,
            description=f"Follow-up from deep research run",
            category=cat,
            priority=50 + i,
        ))
    return items


# ── Main endpoint ─────────────────────────────────────────────────────────

@router.post("/run", response_model=DeepResearchRunResult)
async def run_deep_research(
    req: DeepResearchRequest,
    guest_id: str = Depends(require_guest_id),
):
    run_id = str(uuid.uuid4())[:8]
    inp = req.input

    # 1. Validate + clarify
    clarifications = _validate_and_clarify(req)
    if clarifications:
        return DeepResearchRunResult(
            run_id=run_id, status="needs_clarification",
            clarification_questions=clarifications,
        )

    # 2. Resolve LLM
    try:
        llm = await _resolve_llm(guest_id)
    except Exception as exc:
        logger.error("dr_llm_resolve_failed", error=str(exc))
        return DeepResearchRunResult(
            run_id=run_id, status="failed",
            message="Could not initialize LLM. Check your API key in Settings.",
        )

    # 3. Prepare queries
    queries = _prepare_queries(inp)

    # 4-5. Discover sources
    discovered: list[DiscoveredSourceModel] = []
    if inp.discover_new_sources:
        discovered = await _discover_all(queries)

    # 6. Dedupe + select
    selected, saved_to_add, saved_ids, selected_ids = _dedupe_and_select(
        req.workspace_sources, discovered, inp.use_workspace_sources,
    )

    if not selected:
        return DeepResearchRunResult(
            run_id=run_id, status="blocked",
            discovered_sources=discovered[:20],
            message="No usable sources found. Try enabling source discovery or adding workspace sources.",
        )

    # 7. Resolve section structure (two-stage: generate tailored outline if no existing sections)
    if req.existing_sections:
        sections = [(s.order, s.title, s.content) for s in sorted(req.existing_sections, key=lambda x: x.order)]
        generated_title = None
    else:
        generated_title, tailored_titles = await _generate_tailored_outline(llm, inp, selected)
        sections = [(i, title, "") for i, title in enumerate(tailored_titles)]

    # 8. Draft sections
    updates = await _draft_all_sections(llm, inp, sections, selected)

    # 9. Extract unresolved questions
    unresolved = await _extract_unresolved(llm, updates, inp.topic)

    # 10. Generate follow-up agenda items
    follow_ups = _generate_follow_ups(unresolved)

    drafted_count = sum(1 for u in updates if u.generated_content.strip())
    skipped_count = sum(1 for u in updates if not u.generated_content.strip())

    return DeepResearchRunResult(
        run_id=run_id,
        status="completed",
        generated_title=generated_title,
        generated_outline=[title for _, title, _ in sections] if not req.existing_sections else None,
        section_updates=updates,
        discovered_sources=saved_to_add,
        saved_source_ids=saved_ids,
        selected_source_ids=selected_ids,
        unresolved_questions=unresolved,
        follow_up_items=follow_ups,
        summary=f"Drafted {drafted_count} section(s), skipped {skipped_count}. Used {len(selected_ids)} source(s). Found {len(unresolved)} open question(s).",
        message=None,
    )


# ── Streaming endpoint ────────────────────────────────────────────────────

@router.post("/run/stream")
async def run_deep_research_stream(
    req: DeepResearchRequest,
    guest_id: str = Depends(require_guest_id),
):
    async def event_stream():
        run_id = str(uuid.uuid4())[:8]
        inp = req.input

        def emit(event_type: str, data: dict) -> str:
            payload = json.dumps({"type": event_type, **data})
            return f"data: {payload}\n\n"

        try:
            # 1. Validate
            yield emit("stage", {"stage": "validating", "message": "Validating input..."})
            await asyncio.sleep(0)

            clarifications = _validate_and_clarify(req)
            if clarifications:
                yield emit("result", {
                    "status": "needs_clarification", "run_id": run_id,
                    "clarification_questions": [c.dict() for c in clarifications],
                })
                return

            # 2. Resolve LLM
            try:
                llm = await _resolve_llm(guest_id)
            except Exception as exc:
                logger.error("dr_stream_llm_resolve_failed", error=str(exc))
                yield emit("result", {
                    "status": "failed", "run_id": run_id,
                    "message": "Could not initialize LLM. Check your API key in Settings.",
                })
                return

            # 3. Prepare queries
            yield emit("stage", {"stage": "preparing_queries", "message": "Preparing search queries..."})
            await asyncio.sleep(0)
            queries = _prepare_queries(inp)

            # 4. Discover sources
            yield emit("stage", {"stage": "discovering_sources", "message": "Searching OpenAlex and arXiv..."})
            discovered: list[DiscoveredSourceModel] = []
            if inp.discover_new_sources:
                discovered = await _discover_all(queries)
            yield emit("progress", {
                "stage": "discovering_sources",
                "sources_found": len(discovered),
                "message": f"Found {len(discovered)} candidate sources",
            })

            # 5. Select sources
            yield emit("stage", {"stage": "selecting_sources", "message": "Selecting best sources..."})
            await asyncio.sleep(0)
            selected, saved_to_add, saved_ids, selected_ids = _dedupe_and_select(
                req.workspace_sources, discovered, inp.use_workspace_sources,
            )

            if not selected:
                yield emit("result", {
                    "status": "blocked", "run_id": run_id,
                    "message": "No usable sources found. Try enabling source discovery or adding workspace sources.",
                    "discovered_sources": [d.dict() for d in discovered[:20]],
                })
                return

            yield emit("progress", {
                "stage": "selecting_sources",
                "sources_selected": len(selected),
                "message": f"Selected {len(selected)} sources for drafting",
            })

            # 6. Resolve section structure (two-stage: generate tailored outline if no existing sections)
            if req.existing_sections:
                sections = [(s.order, s.title, s.content) for s in sorted(req.existing_sections, key=lambda x: x.order)]
                generated_title = None
            else:
                yield emit("stage", {"stage": "generating_outline", "message": "Generating tailored outline..."})
                generated_title, tailored_titles = await _generate_tailored_outline(llm, inp, selected)
                sections = [(i, title, "") for i, title in enumerate(tailored_titles)]
                yield emit("tailored_outline", {
                    "generated_title": generated_title,
                    "sections": tailored_titles,
                })

            # 7. Emit sections outline for frontend progress tracking
            yield emit("sections_outline", {
                "titles": [title for _, title, _ in sections],
            })

            # 8. Draft sections — stream per section
            yield emit("stage", {
                "stage": "drafting", "message": "Drafting sections...",
                "total_sections": len(sections),
            })

            updates: list[DRSectionUpdate] = []
            for i, (idx, title, existing_content) in enumerate(sections):
                yield emit("section_start", {"index": i, "title": title})

                if existing_content.strip():
                    update = DRSectionUpdate(
                        section_index=idx, title=title, mode="preview_replace",
                        generated_content="", source_ids_used=[],
                        notes="Section has content; skipped.",
                    )
                    updates.append(update)
                    yield emit("section_complete", {
                        "index": i, "title": title, "skipped": True,
                    })
                    continue

                update = await _draft_single_section(llm, inp, idx, title, sections, selected)
                updates.append(update)

                content_preview = update.generated_content[:200]
                if len(update.generated_content) > 200:
                    content_preview += "..."

                yield emit("section_complete", {
                    "index": i, "title": title,
                    "preview": content_preview,
                    "source_count": len(update.source_ids_used),
                    "skipped": False,
                })

            # 9. Extract unresolved + follow-ups
            yield emit("stage", {"stage": "updating_agenda", "message": "Analyzing gaps and next steps..."})
            unresolved = await _extract_unresolved(llm, updates, inp.topic)
            follow_ups = _generate_follow_ups(unresolved)

            # 10. Final result
            drafted_count = sum(1 for u in updates if u.generated_content.strip())

            yield emit("result", {
                "status": "completed", "run_id": run_id,
                "data": {
                    "section_updates": [u.dict() for u in updates],
                    "discovered_sources": [d.dict() for d in saved_to_add],
                    "saved_source_ids": saved_ids,
                    "selected_source_ids": selected_ids,
                    "unresolved_questions": unresolved,
                    "follow_up_items": [f.dict() for f in follow_ups],
                    "summary": f"Drafted {drafted_count} section(s). Used {len(selected_ids)} source(s). Found {len(unresolved)} open question(s).",
                },
            })

        except Exception as exc:
            logger.exception("dr_stream_unexpected_error", error=str(exc))
            yield emit("result", {
                "status": "failed", "run_id": run_id,
                "message": f"Unexpected error during research run: {str(exc)[:200]}",
            })

    return StreamingResponse(event_stream(), media_type="text/event-stream")
