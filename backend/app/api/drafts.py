import json
import uuid
import asyncio
import structlog
from typing import Any
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.guest import require_guest_id
from app.db.redis_client import get_guest_llm_settings
from app.config import get_settings
from app.llm.client import LLMClient
from app.llm.types import ResolvedLLMSettings

logger = structlog.get_logger()
settings = get_settings()
router = APIRouter()


class SourcePayload(BaseModel):
    id: str
    title: str
    authors: list[str] = []
    year: int | None = None
    abstract: str | None = None
    provider: str = ""
    paper_id: str | None = None


class SectionPayload(BaseModel):
    id: str
    title: str
    content: str
    order: int
    linkedSourceIds: list[str] = []


class DraftRequest(BaseModel):
    action: str  # draft_deliverable | draft_section | revise_section
    workspace_id: str
    deliverable_id: str
    deliverable_type: str
    deliverable_title: str
    sections: list[SectionPayload]
    sources: list[SourcePayload]
    selected_section_id: str | None = None
    revision_instruction: str | None = None
    active_paper_id: str | None = None
    target_section_id: str | None = None  # For inline editing: only regenerate this section


class SectionUpdate(BaseModel):
    sectionId: str
    mode: str  # fill_empty | preview_replace
    generatedContent: str
    sourceIdsUsed: list[str]
    notes: str | None = None


class DraftRunResult(BaseModel):
    runId: str
    action: str
    status: str  # completed | failed | needs_input | blocked
    updates: list[SectionUpdate]
    skippedSectionIds: list[str] = []
    message: str | None = None


TYPE_STYLE = {
    "deep_research": "synthesis-heavy, comparative, concise academic analysis",
    "proposal": "formal research proposal tone with clear motivation, method, and evaluation framing",
    "research_plan": "action-oriented, scoped, concrete next steps and milestones",
    "notes": "compact, practical, less polished — working notes style",
}


def _build_source_context(sources: list[SourcePayload], max_sources: int = 8) -> tuple[str, list[str]]:
    used_ids: list[str] = []
    parts: list[str] = []
    for s in sources[:max_sources]:
        used_ids.append(s.id)
        lines = [f"Title: {s.title}"]
        if s.authors:
            lines.append(f"Authors: {', '.join(s.authors[:4])}")
        if s.year:
            lines.append(f"Year: {s.year}")
        if s.abstract:
            lines.append(f"Abstract: {s.abstract[:400]}")
        if s.provider:
            lines.append(f"Source: {s.provider}")
        parts.append("\n".join(lines))
    return "\n\n---\n\n".join(parts), used_ids


def _prioritize_sources(
    all_sources: list[SourcePayload],
    section: SectionPayload | None,
    sections: list[SectionPayload],
) -> list[SourcePayload]:
    source_map = {s.id: s for s in all_sources}
    ordered: list[SourcePayload] = []
    seen: set[str] = set()

    # 1. Section-linked sources
    if section:
        for sid in section.linkedSourceIds:
            if sid in source_map and sid not in seen:
                ordered.append(source_map[sid])
                seen.add(sid)

    # 2. Deliverable-linked sources
    for sec in sections:
        for sid in sec.linkedSourceIds:
            if sid in source_map and sid not in seen:
                ordered.append(source_map[sid])
                seen.add(sid)

    # 3. Remaining by label priority (core first, then background, then maybe)
    for s in all_sources:
        if s.id not in seen:
            ordered.append(s)
            seen.add(s.id)

    return ordered[:8]


def _section_outline(sections: list[SectionPayload]) -> str:
    return "\n".join(f"{i+1}. {s.title}" for i, s in enumerate(sorted(sections, key=lambda x: x.order)))


async def _resolve_llm(guest_id: str) -> LLMClient:
    stored = await get_guest_llm_settings(guest_id)
    if stored and stored.get("api_key"):
        resolved = ResolvedLLMSettings(
            protocol=stored.get("protocol", "anthropic"),
            api_key=stored["api_key"],
            base_url=stored.get("base_url"),
            model=stored.get("model"),
        )
    else:
        api_key = settings.llm_api_key or settings.anthropic_api_key
        resolved = ResolvedLLMSettings(
            protocol=settings.llm_protocol,
            api_key=api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model or settings.claude_model,
        )
    return LLMClient(resolved)


@router.post("/run", response_model=DraftRunResult)
async def run_draft(
    req: DraftRequest,
    guest_id: str = Depends(require_guest_id),
):
    run_id = str(uuid.uuid4())[:8]
    style = TYPE_STYLE.get(req.deliverable_type, TYPE_STYLE["notes"])

    if not req.sources:
        return DraftRunResult(
            runId=run_id, action=req.action, status="blocked",
            updates=[], message="No workspace sources available. Add or save sources first.",
        )

    try:
        llm = await _resolve_llm(guest_id)
    except Exception as exc:
        logger.error("draft_llm_resolve_failed", error=str(exc))
        return DraftRunResult(
            runId=run_id, action=req.action, status="failed",
            updates=[], message="Could not initialize LLM. Check your API key in Settings.",
        )

    if req.action == "draft_deliverable":
        return await _draft_deliverable(llm, req, run_id, style)
    elif req.action == "draft_section":
        return await _draft_section(llm, req, run_id, style)
    elif req.action == "revise_section":
        return await _revise_section(llm, req, run_id, style)
    else:
        return DraftRunResult(
            runId=run_id, action=req.action, status="failed",
            updates=[], message=f"Unknown action: {req.action}",
        )


async def _draft_deliverable(
    llm: LLMClient, req: DraftRequest, run_id: str, style: str,
) -> DraftRunResult:
    updates: list[SectionUpdate] = []
    skipped: list[str] = []
    outline = _section_outline(req.sections)

    for sec in sorted(req.sections, key=lambda s: s.order):
        if sec.content.strip():
            skipped.append(sec.id)
            continue

        prioritized = _prioritize_sources(req.sources, sec, req.sections)
        source_ctx, used_ids = _build_source_context(prioritized)

        system = (
            f"You are a research writing assistant. Write content for a section of a {req.deliverable_type} document.\n"
            f"Style: {style}\n"
            f"Rules:\n"
            f"- Use ONLY the provided source context. Do not invent claims.\n"
            f"- Be concise and specific. Avoid generic filler.\n"
            f"- Stay conservative when source support is weak.\n"
            f"- Do not repeat points covered in other sections.\n"
            f"- Write 2-4 focused paragraphs.\n"
            f"- Output ONLY the section content text. No markdown headers, no section title."
        )
        user_msg = (
            f"Document: {req.deliverable_title}\n"
            f"Document outline:\n{outline}\n\n"
            f"Write content for section: \"{sec.title}\"\n\n"
            f"Source context:\n{source_ctx}"
        )

        try:
            content = await llm.create_text(
                system=system,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=1200,
                temperature=0.3,
            )
            updates.append(SectionUpdate(
                sectionId=sec.id, mode="fill_empty",
                generatedContent=content.strip(), sourceIdsUsed=used_ids,
            ))
        except Exception as exc:
            logger.warning("draft_section_failed", section=sec.title, error=str(exc))
            updates.append(SectionUpdate(
                sectionId=sec.id, mode="fill_empty",
                generatedContent="", sourceIdsUsed=[],
                notes=f"Generation failed: {str(exc)[:100]}",
            ))

    return DraftRunResult(
        runId=run_id, action="draft_deliverable", status="completed",
        updates=updates, skippedSectionIds=skipped,
        message=f"Drafted {len(updates)} section(s), skipped {len(skipped)} non-empty.",
    )


async def _draft_section(
    llm: LLMClient, req: DraftRequest, run_id: str, style: str,
) -> DraftRunResult:
    sec = next((s for s in req.sections if s.id == req.selected_section_id), None)
    if not sec:
        return DraftRunResult(
            runId=run_id, action="draft_section", status="failed",
            updates=[], message="Selected section not found.",
        )

    prioritized = _prioritize_sources(req.sources, sec, req.sections)
    source_ctx, used_ids = _build_source_context(prioritized)
    outline = _section_outline(req.sections)
    mode = "preview_replace" if sec.content.strip() else "fill_empty"

    system = (
        f"You are a research writing assistant. Write content for a section of a {req.deliverable_type} document.\n"
        f"Style: {style}\n"
        f"Rules:\n"
        f"- Use ONLY the provided source context. Do not invent claims.\n"
        f"- Be concise and specific. Avoid generic filler.\n"
        f"- Stay conservative when source support is weak.\n"
        f"- Write 2-4 focused paragraphs.\n"
        f"- Output ONLY the section content text. No markdown headers, no section title."
    )
    user_msg = (
        f"Document: {req.deliverable_title}\n"
        f"Document outline:\n{outline}\n\n"
        f"Write content for section: \"{sec.title}\"\n\n"
        f"Source context:\n{source_ctx}"
    )

    try:
        content = await llm.create_text(
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=1200,
            temperature=0.3,
        )
        return DraftRunResult(
            runId=run_id, action="draft_section", status="completed",
            updates=[SectionUpdate(
                sectionId=sec.id, mode=mode,
                generatedContent=content.strip(), sourceIdsUsed=used_ids,
            )],
        )
    except Exception as exc:
        return DraftRunResult(
            runId=run_id, action="draft_section", status="failed",
            updates=[], message=f"Generation failed: {str(exc)[:200]}",
        )


async def _revise_section(
    llm: LLMClient, req: DraftRequest, run_id: str, style: str,
) -> DraftRunResult:
    sec = next((s for s in req.sections if s.id == req.selected_section_id), None)
    if not sec:
        return DraftRunResult(
            runId=run_id, action="revise_section", status="failed",
            updates=[], message="Selected section not found.",
        )
    if not req.revision_instruction:
        return DraftRunResult(
            runId=run_id, action="revise_section", status="needs_input",
            updates=[], message="Enter a revision instruction.",
        )

    prioritized = _prioritize_sources(req.sources, sec, req.sections)
    source_ctx, used_ids = _build_source_context(prioritized)
    outline = _section_outline(req.sections)

    system = (
        f"You are a research writing assistant. Revise a section of a {req.deliverable_type} document.\n"
        f"Style: {style}\n"
        f"Rules:\n"
        f"- Use ONLY the provided source context. Do not invent claims.\n"
        f"- Follow the revision instruction precisely.\n"
        f"- Stay conservative when source support is weak.\n"
        f"- Output ONLY the revised section content text. No markdown headers, no section title."
    )
    user_msg = (
        f"Document: {req.deliverable_title}\n"
        f"Document outline:\n{outline}\n\n"
        f"Section: \"{sec.title}\"\n"
        f"Current content:\n{sec.content}\n\n"
        f"Revision instruction: {req.revision_instruction}\n\n"
        f"Source context:\n{source_ctx}"
    )

    try:
        content = await llm.create_text(
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=1500,
            temperature=0.3,
        )
        return DraftRunResult(
            runId=run_id, action="revise_section", status="completed",
            updates=[SectionUpdate(
                sectionId=sec.id, mode="preview_replace",
                generatedContent=content.strip(), sourceIdsUsed=used_ids,
            )],
        )
    except Exception as exc:
        return DraftRunResult(
            runId=run_id, action="revise_section", status="failed",
            updates=[], message=f"Revision failed: {str(exc)[:200]}",
        )


# ── SSE streaming endpoint ─────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.post("/run/stream")
async def run_draft_stream(
    req: DraftRequest,
    guest_id: str = Depends(require_guest_id),
):
    """SSE streaming version of /run — emits progress events per section."""

    async def generate():
        run_id = str(uuid.uuid4())[:8]
        style = TYPE_STYLE.get(req.deliverable_type, TYPE_STYLE["notes"])

        if not req.sources:
            yield _sse({"type": "blocked", "message": "No workspace sources available."})
            return

        try:
            llm = await _resolve_llm(guest_id)
        except Exception as exc:
            yield _sse({"type": "error", "message": f"Could not initialize LLM: {str(exc)[:100]}"})
            return

        yield _sse({"type": "stage", "stage": "preparing", "message": "Preparing draft..."})
        await asyncio.sleep(0)

        if req.action == "draft_deliverable":
            yield _sse({"type": "stage", "stage": "generating", "message": "Generating sections..."})

            sections_sorted = sorted(req.sections, key=lambda s: s.order)
            titles = [s.title for s in sections_sorted]
            yield _sse({"type": "sections_outline", "titles": titles})

            updates: list[dict] = []
            skipped: list[str] = []
            outline = _section_outline(req.sections)

            for idx, sec in enumerate(sections_sorted):
                if sec.content.strip():
                    skipped.append(sec.id)
                    yield _sse({"type": "section_skipped", "index": idx, "section_id": sec.id})
                    continue

                yield _sse({"type": "section_start", "index": idx, "section_id": sec.id})

                prioritized = _prioritize_sources(req.sources, sec, req.sections)
                source_ctx, used_ids = _build_source_context(prioritized)

                system = (
                    f"You are a research writing assistant. Write content for a section of a {req.deliverable_type} document.\n"
                    f"Style: {style}\n"
                    f"Rules:\n"
                    f"- Use ONLY the provided source context. Do not invent claims.\n"
                    f"- Be concise and specific. Avoid generic filler.\n"
                    f"- Stay conservative when source support is weak.\n"
                    f"- Do not repeat points covered in other sections.\n"
                    f"- Write 2-4 focused paragraphs.\n"
                    f"- Output ONLY the section content text. No markdown headers, no section title."
                )
                user_msg = (
                    f"Document: {req.deliverable_title}\n"
                    f"Document outline:\n{outline}\n\n"
                    f"Write content for section: \"{sec.title}\"\n\n"
                    f"Source context:\n{source_ctx}"
                )

                try:
                    content = await llm.create_text(
                        system=system,
                        messages=[{"role": "user", "content": user_msg}],
                        max_tokens=1200,
                        temperature=0.3,
                    )
                    preview = content.strip()[:120] + "..." if len(content.strip()) > 120 else content.strip()
                    updates.append({
                        "sectionId": sec.id, "mode": "fill_empty",
                        "generatedContent": content.strip(), "sourceIdsUsed": used_ids,
                    })
                    yield _sse({"type": "section_complete", "index": idx, "section_id": sec.id, "preview": preview})
                except Exception as exc:
                    logger.warning("draft_stream_section_failed", section=sec.title, error=str(exc))
                    yield _sse({"type": "section_complete", "index": idx, "section_id": sec.id, "preview": f"Failed: {str(exc)[:60]}"})

            yield _sse({"type": "result", "data": {
                "runId": run_id, "action": "draft_deliverable", "status": "completed",
                "updates": updates, "skippedSectionIds": skipped,
                "message": f"Drafted {len(updates)} section(s), skipped {len(skipped)} non-empty.",
            }})

        elif req.action in ("draft_section", "revise_section"):
            sec = next((s for s in req.sections if s.id == req.selected_section_id), None)
            if not sec:
                yield _sse({"type": "error", "message": "Selected section not found."})
                return

            yield _sse({"type": "stage", "stage": "generating", "message": f"{'Revising' if req.action == 'revise_section' else 'Drafting'} section..."})
            yield _sse({"type": "section_start", "index": 0, "section_id": sec.id})

            prioritized = _prioritize_sources(req.sources, sec, req.sections)
            source_ctx, used_ids = _build_source_context(prioritized)
            outline = _section_outline(req.sections)

            if req.action == "revise_section":
                if not req.revision_instruction:
                    yield _sse({"type": "error", "message": "Enter a revision instruction."})
                    return
                system = (
                    f"You are a research writing assistant. Revise a section of a {req.deliverable_type} document.\n"
                    f"Style: {style}\n"
                    f"Rules:\n"
                    f"- Use ONLY the provided source context. Do not invent claims.\n"
                    f"- Follow the revision instruction precisely.\n"
                    f"- Stay conservative when source support is weak.\n"
                    f"- Output ONLY the revised section content text. No markdown headers, no section title."
                )
                user_msg = (
                    f"Document: {req.deliverable_title}\n"
                    f"Document outline:\n{outline}\n\n"
                    f"Section: \"{sec.title}\"\n"
                    f"Current content:\n{sec.content}\n\n"
                    f"Revision instruction: {req.revision_instruction}\n\n"
                    f"Source context:\n{source_ctx}"
                )
                mode = "preview_replace"
            else:
                system = (
                    f"You are a research writing assistant. Write content for a section of a {req.deliverable_type} document.\n"
                    f"Style: {style}\n"
                    f"Rules:\n"
                    f"- Use ONLY the provided source context. Do not invent claims.\n"
                    f"- Be concise and specific. Avoid generic filler.\n"
                    f"- Stay conservative when source support is weak.\n"
                    f"- Write 2-4 focused paragraphs.\n"
                    f"- Output ONLY the section content text. No markdown headers, no section title."
                )
                user_msg = (
                    f"Document: {req.deliverable_title}\n"
                    f"Document outline:\n{outline}\n\n"
                    f"Write content for section: \"{sec.title}\"\n\n"
                    f"Source context:\n{source_ctx}"
                )
                mode = "preview_replace" if sec.content.strip() else "fill_empty"

            try:
                content = await llm.create_text(
                    system=system,
                    messages=[{"role": "user", "content": user_msg}],
                    max_tokens=1500,
                    temperature=0.3,
                )
                preview = content.strip()[:120] + "..." if len(content.strip()) > 120 else content.strip()
                yield _sse({"type": "section_complete", "index": 0, "section_id": sec.id, "preview": preview})
                yield _sse({"type": "result", "data": {
                    "runId": run_id, "action": req.action, "status": "completed",
                    "updates": [{"sectionId": sec.id, "mode": mode, "generatedContent": content.strip(), "sourceIdsUsed": used_ids}],
                    "skippedSectionIds": [],
                }})
            except Exception as exc:
                yield _sse({"type": "error", "message": f"Generation failed: {str(exc)[:200]}"})
        else:
            yield _sse({"type": "error", "message": f"Unknown action: {req.action}"})

    return StreamingResponse(generate(), media_type="text/event-stream")
