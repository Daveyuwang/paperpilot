import json
import uuid
import structlog
import asyncio
from typing import Any
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.guest import require_guest_id
from app.api.drafts import _resolve_llm, _build_source_context, SourcePayload

logger = structlog.get_logger()
router = APIRouter()

# ── Request models ────────────────────────────────────────────────────────

class ProposalPlanInput(BaseModel):
    mode: str  # proposal | research_plan
    topic: str
    problem_statement: str | None = None
    focus: str | None = None
    target_deliverable_id: str | None = None
    use_workspace_sources: bool = True
    use_deep_research_context: bool = False
    deep_research_deliverable_ids: list[str] = []
    notes: str | None = None
    # Proposal-specific
    motivation: str | None = None
    proposed_idea: str | None = None
    evaluation_direction: str | None = None
    constraints: str | None = None
    # Research-plan-specific
    planning_horizon: str | None = None  # 1_week | 2_weeks | 1_month | custom
    intended_deliverables: str | None = None
    risks: str | None = None
    milestone_notes: str | None = None


class PPSourcePayload(BaseModel):
    id: str
    title: str
    authors: list[str] = []
    year: int | None = None
    abstract: str | None = None
    provider: str = ""
    paper_id: str | None = None
    label: str = "maybe"


class PPSectionPayload(BaseModel):
    id: str
    title: str
    content: str
    order: int
    linkedSourceIds: list[str] = []


class DRContextPayload(BaseModel):
    deliverable_id: str
    title: str
    sections: list[PPSectionPayload] = []


class ProposalPlanRequest(BaseModel):
    input: ProposalPlanInput
    workspace_id: str
    workspace_sources: list[PPSourcePayload] = []
    existing_sections: list[PPSectionPayload] = []
    deep_research_context: list[DRContextPayload] = []
    active_paper_id: str | None = None


# ── Response models ───────────────────────────────────────────────────────

class ClarificationQuestion(BaseModel):
    field: str
    question: str
    suggestion: str | None = None


class PPSectionUpdate(BaseModel):
    section_id: str
    mode: str  # fill_empty | preview_replace
    generated_content: str
    source_ids_used: list[str]
    notes: str | None = None


class FollowUpItem(BaseModel):
    title: str
    description: str | None = None
    category: str | None = None
    priority: int = 50


class ProposalPlanRunResult(BaseModel):
    run_id: str
    mode: str
    status: str  # completed | needs_clarification | failed | blocked
    clarification_questions: list[ClarificationQuestion] = []
    generated_title: str | None = None
    generated_outline: list[str] | None = None
    section_updates: list[PPSectionUpdate] = []
    updated_section_ids: list[str] = []
    skipped_section_ids: list[str] = []
    selected_source_ids: list[str] = []
    deep_research_context_ids: list[str] = []
    unresolved_questions: list[str] = []
    follow_up_items: list[FollowUpItem] = []
    summary: str | None = None
    message: str | None = None


# ── Section templates ─────────────────────────────────────────────────────

PROPOSAL_SECTIONS = [
    "Problem", "Motivation", "Related Work", "Proposed Idea",
    "Method Sketch", "Evaluation Plan", "Risks / Limitations",
]

RESEARCH_PLAN_SECTIONS = [
    "Goal", "Research Questions", "Source Collection Plan",
    "Reading Plan", "Deliverables", "Risks", "Milestones",
]

# PLACEHOLDER_SECTION_INTENT

PROPOSAL_INTENT = {
    "Problem": (
        "Define the research problem precisely. Bound the scope. "
        "Avoid generic broad openings. State what this proposal addresses and what it does not."
    ),
    "Motivation": (
        "Explain why this problem matters. Tie to an observed limitation, gap, or opportunity. "
        "Be specific about what is missing in current approaches."
    ),
    "Related Work": (
        "Cluster prior work by approach family. Emphasize what is missing or the relevant contrast. "
        "Avoid long list-like dumping. Highlight the gap this proposal fills."
    ),
    "Proposed Idea": (
        "State the central idea clearly and concisely. "
        "Distinguish from existing work where possible. Be concrete."
    ),
    "Method Sketch": (
        "Describe the intended approach at a high level. "
        "Keep concrete but not implementation-heavy. Outline key steps or components."
    ),
    "Evaluation Plan": (
        "Describe how the idea could be evaluated. "
        "Mention likely comparisons, metrics, or setups if supported by sources."
    ),
    "Risks / Limitations": (
        "Identify plausible risks or uncertainty honestly. "
        "Avoid invented confidence. Acknowledge evidence gaps."
    ),
}

RESEARCH_PLAN_INTENT = {
    "Goal": (
        "Define the near-term research goal clearly. "
        "Be specific about what should be accomplished and by when."
    ),
    "Research Questions": (
        "List the concrete questions to answer before experimentation. "
        "Each question should be specific and actionable."
    ),
    "Source Collection Plan": (
        "Describe what material to gather, compare, or prioritize. "
        "Reference existing workspace sources where relevant."
    ),
    "Reading Plan": (
        "Describe how to sequence reading and synthesis. "
        "Prioritize by relevance and dependency."
    ),
    "Deliverables": (
        "List what artifacts the researcher should produce. "
        "Be concrete about format and scope."
    ),
    "Risks": (
        "Identify key blockers, uncertainty, or dependencies. "
        "Be honest about what could go wrong."
    ),
    "Milestones": (
        "Define bounded next steps and checkpoints. "
        "Keep milestones concrete and time-aware if a planning horizon was given."
    ),
}

LABEL_PRIORITY = {"core": 0, "background": 1, "general": 2, "": 3}


# ── Tailored outline generation ───────────────────────────────────────────

async def _generate_tailored_outline(
    llm: Any,
    inp: ProposalPlanInput,
    sources: list[SourcePayload],
    dr_context_text: str,
) -> tuple[str, list[str]]:
    """Stage 1: Generate a document title and tailored section outline.

    Uses the base template as a scaffold but allows the LLM to rename,
    add (up to 2 extra), or drop sections based on the topic and sources.
    Returns (generated_title, list_of_section_titles).
    """
    base_sections = PROPOSAL_SECTIONS if inp.mode == "proposal" else RESEARCH_PLAN_SECTIONS
    doc_type = "research proposal" if inp.mode == "proposal" else "research plan"

    source_summaries = []
    for s in sources[:6]:
        line = s.title
        if s.year:
            line += f" ({s.year})"
        if s.abstract:
            line += f" — {s.abstract[:120]}"
        source_summaries.append(line)
    source_block = "\n".join(f"- {s}" for s in source_summaries) if source_summaries else "(no sources yet)"

    base_sections_str = "\n".join(f"- {s}" for s in base_sections)

    system = (
        f"You are a research document architect. Given a research topic and available sources, "
        f"produce a tailored document title and section outline for a {doc_type}.\n\n"
        f"Rules:\n"
        f"- Start from the base section template below. You may rename sections, drop irrelevant ones, "
        f"or add up to 2 new sections — but keep the total between 3 and 8 sections.\n"
        f"- The title should be specific to the topic, not generic (e.g. 'Proposal: Multi-Modal RAG for Clinical Notes' not 'Proposal Draft').\n"
        f"- Section titles should be concise (2-5 words each).\n"
        f"- Return valid JSON: {{\"title\": \"...\", \"sections\": [\"...\", ...]}}\n"
        f"- Do NOT include section content, only titles."
    )
    user_msg = (
        f"Topic: {inp.topic}\n"
        + (f"Focus: {inp.focus}\n" if inp.focus else "")
        + (f"Problem statement: {inp.problem_statement}\n" if inp.problem_statement else "")
        + (f"Proposed idea: {inp.proposed_idea}\n" if inp.proposed_idea else "")
        + f"\nBase section template:\n{base_sections_str}\n"
        f"\nAvailable sources:\n{source_block}"
    )
    if dr_context_text:
        user_msg += f"\n\nDeep research context (summary):\n{dr_context_text[:500]}"

    try:
        result = await llm.create_json(
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=400,
            temperature=0.3,
        )
        if isinstance(result, dict):
            title = result.get("title", f"{'Proposal Draft' if inp.mode == 'proposal' else 'Research Plan'}")
            sections = result.get("sections", base_sections[:])
            if not isinstance(sections, list) or len(sections) < 3:
                sections = base_sections[:]
            sections = [str(s) for s in sections[:8]]
            return str(title), sections
    except Exception as exc:
        logger.warning("pp_tailored_outline_failed", error=str(exc))

    default_title = "Proposal Draft" if inp.mode == "proposal" else "Research Plan"
    return default_title, base_sections[:]


# ── Pipeline helpers ─────────────────────────────────────────────────────


def _validate_and_clarify(req: ProposalPlanRequest) -> list[ClarificationQuestion]:
    questions: list[ClarificationQuestion] = []
    inp = req.input
    topic = inp.topic.strip()

    if not topic:
        questions.append(ClarificationQuestion(
            field="topic",
            question="What research topic or question is this about?",
        ))
        return questions

    if not inp.use_workspace_sources and not inp.use_deep_research_context:
        questions.append(ClarificationQuestion(
            field="sources",
            question="Both source options are disabled. Enable workspace sources, deep research context, or both.",
        ))

    if inp.target_deliverable_id and not req.existing_sections:
        questions.append(ClarificationQuestion(
            field="deliverable",
            question="The selected deliverable has no sections. Create a new deliverable instead?",
        ))

    if inp.mode == "proposal":
        words = topic.split()
        if len(words) <= 2 and not inp.problem_statement and not inp.focus:
            questions.append(ClarificationQuestion(
                field="problem_statement",
                question="The topic is quite broad. What specific problem are you trying to address?",
                suggestion="Add a problem statement or focus to narrow the scope.",
            ))
        if not inp.motivation and not inp.problem_statement and not inp.notes:
            questions.append(ClarificationQuestion(
                field="motivation",
                question="What motivates this proposal? A brief motivation helps ground the writing.",
            ))

    if inp.mode == "research_plan":
        if not inp.planning_horizon and not inp.intended_deliverables and not inp.risks:
            questions.append(ClarificationQuestion(
                field="planning_horizon",
                question="What time horizon should the research plan cover?",
                suggestion="e.g. 1 week, 2 weeks, 1 month",
            ))

    return questions


def _select_sources(
    workspace_sources: list[PPSourcePayload],
    use_workspace: bool,
) -> tuple[list[SourcePayload], list[str]]:
    if not use_workspace:
        return [], []

    ws_sorted = sorted(
        workspace_sources,
        key=lambda s: LABEL_PRIORITY.get(s.label, 3),
    )

    selected: list[SourcePayload] = []
    selected_ids: list[str] = []
    for s in ws_sorted[:8]:
        selected.append(SourcePayload(
            id=s.id, title=s.title, authors=s.authors,
            year=s.year, abstract=s.abstract, provider=s.provider,
            paper_id=s.paper_id,
        ))
        selected_ids.append(s.id)

    return selected, selected_ids


def _build_dr_context(dr_deliverables: list[DRContextPayload]) -> tuple[str, list[str]]:
    if not dr_deliverables:
        return "", []

    parts: list[str] = []
    used_ids: list[str] = []
    for dr in dr_deliverables[:2]:
        used_ids.append(dr.deliverable_id)
        sections_text = []
        for sec in sorted(dr.sections, key=lambda s: s.order):
            if sec.content.strip():
                sections_text.append(f"### {sec.title}\n{sec.content[:600]}")
        if sections_text:
            parts.append(f"## {dr.title}\n" + "\n\n".join(sections_text))

    return "\n\n---\n\n".join(parts), used_ids

# PLACEHOLDER_DRAFT_AND_ENDPOINT


async def _draft_sections(
    llm: Any,
    inp: ProposalPlanInput,
    sections: list[tuple[str, str, str]],  # (id, title, existing_content)
    sources: list[SourcePayload],
    dr_context_text: str,
) -> tuple[list[PPSectionUpdate], list[str], list[str]]:
    source_ctx, used_ids = _build_source_context(sources)

    if inp.mode == "proposal":
        style = "formal research proposal tone with clear motivation, method, and evaluation framing"
        intent_map = PROPOSAL_INTENT
    else:
        style = "action-oriented, scoped, concrete next steps and milestones"
        intent_map = RESEARCH_PLAN_INTENT

    outline = "\n".join(f"{i+1}. {title}" for i, (_, title, _) in enumerate(sections))

    extra_context_parts: list[str] = []
    if inp.problem_statement:
        extra_context_parts.append(f"Problem statement: {inp.problem_statement}")
    if inp.motivation:
        extra_context_parts.append(f"Motivation: {inp.motivation}")
    if inp.proposed_idea:
        extra_context_parts.append(f"Proposed idea: {inp.proposed_idea}")
    if inp.evaluation_direction:
        extra_context_parts.append(f"Evaluation direction: {inp.evaluation_direction}")
    if inp.constraints:
        extra_context_parts.append(f"Constraints: {inp.constraints}")
    if inp.planning_horizon:
        extra_context_parts.append(f"Planning horizon: {inp.planning_horizon}")
    if inp.intended_deliverables:
        extra_context_parts.append(f"Intended deliverables: {inp.intended_deliverables}")
    if inp.risks:
        extra_context_parts.append(f"Known risks: {inp.risks}")
    if inp.milestone_notes:
        extra_context_parts.append(f"Milestone notes: {inp.milestone_notes}")
    if inp.notes:
        extra_context_parts.append(f"Additional notes: {inp.notes}")
    extra_context = "\n".join(extra_context_parts)

    updates: list[PPSectionUpdate] = []
    updated_ids: list[str] = []
    skipped_ids: list[str] = []

    for sec_id, title, existing_content in sections:
        if existing_content.strip():
            skipped_ids.append(sec_id)
            continue

        intent = intent_map.get(title, "Write focused, grounded content for this section.")
        system = (
            f"You are a research writing assistant producing a {inp.mode.replace('_', ' ')}.\n"
            f"Style: {style}\n"
            f"Section purpose: {intent}\n"
            f"Rules:\n"
            f"- Use ONLY the provided source context and deep research synthesis. Do not invent claims.\n"
            f"- Be concise and specific. Avoid generic filler.\n"
            f"- Stay conservative when source support is weak.\n"
            f"- Write 2-4 focused paragraphs.\n"
            f"- Output ONLY the section content text. No markdown headers, no section title."
        )
        user_msg = (
            f"Topic: {inp.topic}\n"
            + (f"Focus: {inp.focus}\n" if inp.focus else "")
            + (f"\n{extra_context}\n" if extra_context else "")
            + f"\nDocument outline:\n{outline}\n\n"
            f"Write content for section: \"{title}\"\n\n"
            f"Source context:\n{source_ctx}"
        )
        if dr_context_text:
            user_msg += f"\n\nDeep research synthesis (secondary context):\n{dr_context_text}"

        try:
            content = await llm.create_text(
                system=system,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=1200,
                temperature=0.3,
            )
            updates.append(PPSectionUpdate(
                section_id=sec_id, mode="fill_empty",
                generated_content=content.strip(), source_ids_used=used_ids,
            ))
            updated_ids.append(sec_id)
        except Exception as exc:
            logger.warning("pp_draft_section_failed", section=title, error=str(exc))
            updates.append(PPSectionUpdate(
                section_id=sec_id, mode="fill_empty",
                generated_content="", source_ids_used=[],
                notes=f"Generation failed: {str(exc)[:100]}",
            ))

    return updates, updated_ids, skipped_ids


async def _extract_unresolved(llm: Any, updates: list[PPSectionUpdate], topic: str, mode: str) -> list[str]:
    drafted_text = "\n\n".join(
        f"## {u.section_id}\n{u.generated_content}"
        for u in updates if u.generated_content.strip()
    )
    if not drafted_text.strip():
        return []

    doc_type = "proposal" if mode == "proposal" else "research plan"
    system = (
        f"You are a research analyst. Given a drafted {doc_type}, "
        "extract 1-5 specific unresolved questions, evidence gaps, or areas needing clarification. "
        "Return a JSON array of strings. Each string should be a concrete, actionable question."
    )
    user_msg = f"Topic: {topic}\n\nDrafted content:\n{drafted_text}"

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
        logger.warning("pp_extract_unresolved_failed", error=str(exc))
        return []


def _generate_follow_ups(unresolved: list[str], mode: str) -> list[FollowUpItem]:
    items: list[FollowUpItem] = []
    for i, q in enumerate(unresolved[:3]):
        if mode == "proposal":
            cat = "approach" if any(w in q.lower() for w in ["method", "approach", "technique"]) \
                else "experiments" if any(w in q.lower() for w in ["evaluat", "benchmark", "experiment"]) \
                else "motivation" if any(w in q.lower() for w in ["why", "motivation", "gap"]) \
                else "custom"
        else:
            cat = "approach" if any(w in q.lower() for w in ["method", "approach", "collect"]) \
                else "experiments" if any(w in q.lower() for w in ["milestone", "deliverable", "timeline"]) \
                else "custom"
        items.append(FollowUpItem(
            title=q,
            description=f"Follow-up from {mode.replace('_', ' ')} run",
            category=cat,
            priority=50 + i,
        ))
    return items


# ── Main endpoint ─────────────────────────────────────────────────────────

@router.post("/run", response_model=ProposalPlanRunResult)
async def run_proposal_plan(
    req: ProposalPlanRequest,
    guest_id: str = Depends(require_guest_id),
):
    run_id = str(uuid.uuid4())[:8]
    inp = req.input

    # 1. Validate
    clarifications = _validate_and_clarify(req)
    if clarifications:
        return ProposalPlanRunResult(
            run_id=run_id, mode=inp.mode, status="needs_clarification",
            clarification_questions=clarifications,
        )

    # 2. Resolve LLM
    try:
        llm = await _resolve_llm(guest_id)
    except Exception as exc:
        logger.error("pp_llm_resolve_failed", error=str(exc))
        return ProposalPlanRunResult(
            run_id=run_id, mode=inp.mode, status="failed",
            message="Could not initialize LLM. Check your API key in Settings.",
        )

    # 3. Select sources
    selected, selected_ids = _select_sources(req.workspace_sources, inp.use_workspace_sources)

    # 4. Build deep research context
    dr_context_text, dr_context_ids = _build_dr_context(req.deep_research_context)

    if not selected and not dr_context_text:
        return ProposalPlanRunResult(
            run_id=run_id, mode=inp.mode, status="blocked",
            message="No sources or deep research context available. Enable workspace sources or deep research context.",
        )

    # 5. Resolve sections (two-stage: generate tailored outline if no existing sections)
    if req.existing_sections:
        sections = [(s.id, s.title, s.content) for s in sorted(req.existing_sections, key=lambda x: x.order)]
        generated_title = None
    else:
        generated_title, tailored_titles = await _generate_tailored_outline(llm, inp, selected, dr_context_text)
        sections = [(f"new-{i}", title, "") for i, title in enumerate(tailored_titles)]

    # 6. Draft sections
    updates, updated_ids, skipped_ids = await _draft_sections(llm, inp, sections, selected, dr_context_text)

    # 7. Extract unresolved
    unresolved = await _extract_unresolved(llm, updates, inp.topic, inp.mode)

    # 8. Generate follow-ups
    follow_ups = _generate_follow_ups(unresolved, inp.mode)

    drafted_count = sum(1 for u in updates if u.generated_content.strip())
    doc_type = "proposal" if inp.mode == "proposal" else "research plan"

    return ProposalPlanRunResult(
        run_id=run_id,
        mode=inp.mode,
        status="completed",
        generated_title=generated_title,
        generated_outline=[title for _, title, _ in sections] if not req.existing_sections else None,
        section_updates=updates,
        updated_section_ids=updated_ids,
        skipped_section_ids=skipped_ids,
        selected_source_ids=selected_ids,
        deep_research_context_ids=dr_context_ids,
        unresolved_questions=unresolved,
        follow_up_items=follow_ups,
        summary=f"Drafted {drafted_count} {doc_type} section(s), skipped {len(skipped_ids)}. Used {len(selected_ids)} source(s).",
    )


# ── Streaming endpoint ────────────────────────────────────────────────────

@router.post("/run/stream")
async def run_proposal_plan_stream(
    req: ProposalPlanRequest,
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
                    "status": "needs_clarification", "run_id": run_id, "mode": inp.mode,
                    "clarification_questions": [c.dict() for c in clarifications],
                })
                return

            # 2. Resolve LLM
            try:
                llm = await _resolve_llm(guest_id)
            except Exception as exc:
                logger.error("pp_stream_llm_resolve_failed", error=str(exc))
                yield emit("result", {
                    "status": "failed", "run_id": run_id, "mode": inp.mode,
                    "message": "Could not initialize LLM. Check your API key in Settings.",
                })
                return

            # 3. Select sources
            yield emit("stage", {"stage": "selecting_context", "message": "Selecting sources and context..."})
            await asyncio.sleep(0)
            selected, selected_ids = _select_sources(req.workspace_sources, inp.use_workspace_sources)
            dr_context_text, dr_context_ids = _build_dr_context(req.deep_research_context)

            if not selected and not dr_context_text:
                yield emit("result", {
                    "status": "blocked", "run_id": run_id, "mode": inp.mode,
                    "message": "No sources or deep research context available.",
                })
                return

            yield emit("progress", {
                "stage": "selecting_context",
                "sources_selected": len(selected),
                "message": f"Using {len(selected)} sources" + (f" + {len(dr_context_ids)} deep research context(s)" if dr_context_ids else ""),
            })

            # 4. Resolve sections (two-stage: generate tailored outline if no existing sections)
            if req.existing_sections:
                sections = [(s.id, s.title, s.content) for s in sorted(req.existing_sections, key=lambda x: x.order)]
            else:
                yield emit("stage", {"stage": "generating_outline", "message": "Generating tailored outline..."})
                generated_title, tailored_titles = await _generate_tailored_outline(llm, inp, selected, dr_context_text)
                sections = [(f"new-{i}", title, "") for i, title in enumerate(tailored_titles)]
                yield emit("tailored_outline", {
                    "generated_title": generated_title,
                    "sections": tailored_titles,
                })

            # Emit sections outline for frontend progress tracking
            yield emit("sections_outline", {
                "titles": [title for _, title, _ in sections],
            })

            # 5. Draft sections — stream per section
            yield emit("stage", {
                "stage": "drafting", "message": "Drafting sections...",
                "total_sections": len(sections),
            })

            source_ctx, used_ids = _build_source_context(selected)
            if inp.mode == "proposal":
                style = "formal research proposal tone with clear motivation, method, and evaluation framing"
                intent_map = PROPOSAL_INTENT
            else:
                style = "action-oriented, scoped, concrete next steps and milestones"
                intent_map = RESEARCH_PLAN_INTENT

            outline = "\n".join(f"{i+1}. {title}" for i, (_, title, _) in enumerate(sections))

            extra_context_parts: list[str] = []
            if inp.problem_statement: extra_context_parts.append(f"Problem statement: {inp.problem_statement}")
            if inp.motivation: extra_context_parts.append(f"Motivation: {inp.motivation}")
            if inp.proposed_idea: extra_context_parts.append(f"Proposed idea: {inp.proposed_idea}")
            if inp.evaluation_direction: extra_context_parts.append(f"Evaluation direction: {inp.evaluation_direction}")
            if inp.constraints: extra_context_parts.append(f"Constraints: {inp.constraints}")
            if inp.planning_horizon: extra_context_parts.append(f"Planning horizon: {inp.planning_horizon}")
            if inp.intended_deliverables: extra_context_parts.append(f"Intended deliverables: {inp.intended_deliverables}")
            if inp.risks: extra_context_parts.append(f"Known risks: {inp.risks}")
            if inp.milestone_notes: extra_context_parts.append(f"Milestone notes: {inp.milestone_notes}")
            if inp.notes: extra_context_parts.append(f"Additional notes: {inp.notes}")
            extra_context = "\n".join(extra_context_parts)

            updates: list[PPSectionUpdate] = []
            updated_ids: list[str] = []
            skipped_ids: list[str] = []

            for i, (sec_id, title, existing_content) in enumerate(sections):
                yield emit("section_start", {"index": i, "title": title})

                if existing_content.strip():
                    skipped_ids.append(sec_id)
                    yield emit("section_complete", {
                        "index": i, "title": title, "skipped": True,
                    })
                    continue

                intent = intent_map.get(title, "Write focused, grounded content for this section.")
                system = (
                    f"You are a research writing assistant producing a {inp.mode.replace('_', ' ')}.\n"
                    f"Style: {style}\n"
                    f"Section purpose: {intent}\n"
                    f"Rules:\n"
                    f"- Use ONLY the provided source context and deep research synthesis. Do not invent claims.\n"
                    f"- Be concise and specific. Avoid generic filler.\n"
                    f"- Stay conservative when source support is weak.\n"
                    f"- Write 2-4 focused paragraphs.\n"
                    f"- Output ONLY the section content text. No markdown headers, no section title."
                )
                user_msg = (
                    f"Topic: {inp.topic}\n"
                    + (f"Focus: {inp.focus}\n" if inp.focus else "")
                    + (f"\n{extra_context}\n" if extra_context else "")
                    + f"\nDocument outline:\n{outline}\n\n"
                    f"Write content for section: \"{title}\"\n\n"
                    f"Source context:\n{source_ctx}"
                )
                if dr_context_text:
                    user_msg += f"\n\nDeep research synthesis (secondary context):\n{dr_context_text}"

                try:
                    content = await llm.create_text(
                        system=system,
                        messages=[{"role": "user", "content": user_msg}],
                        max_tokens=1200,
                        temperature=0.3,
                    )
                    update = PPSectionUpdate(
                        section_id=sec_id, mode="fill_empty",
                        generated_content=content.strip(), source_ids_used=used_ids,
                    )
                    updates.append(update)
                    updated_ids.append(sec_id)

                    content_preview = content.strip()[:200]
                    if len(content.strip()) > 200:
                        content_preview += "..."

                    yield emit("section_complete", {
                        "index": i, "title": title,
                        "preview": content_preview,
                        "source_count": len(used_ids),
                        "skipped": False,
                    })
                except Exception as exc:
                    logger.warning("pp_stream_draft_failed", section=title, error=str(exc))
                    updates.append(PPSectionUpdate(
                        section_id=sec_id, mode="fill_empty",
                        generated_content="", source_ids_used=[],
                        notes=f"Generation failed: {str(exc)[:100]}",
                    ))
                    yield emit("section_complete", {
                        "index": i, "title": title, "skipped": False,
                        "preview": "",
                    })

            # 6. Extract unresolved + follow-ups
            yield emit("stage", {"stage": "updating_agenda", "message": "Analyzing gaps and next steps..."})
            unresolved = await _extract_unresolved(llm, updates, inp.topic, inp.mode)
            follow_ups = _generate_follow_ups(unresolved, inp.mode)

            drafted_count = sum(1 for u in updates if u.generated_content.strip())
            doc_type = "proposal" if inp.mode == "proposal" else "research plan"

            yield emit("result", {
                "status": "completed", "run_id": run_id, "mode": inp.mode,
                "data": {
                    "section_updates": [u.dict() for u in updates],
                    "updated_section_ids": updated_ids,
                    "skipped_section_ids": skipped_ids,
                    "selected_source_ids": selected_ids,
                    "deep_research_context_ids": dr_context_ids,
                    "unresolved_questions": unresolved,
                    "follow_up_items": [f.dict() for f in follow_ups],
                    "summary": f"Drafted {drafted_count} {doc_type} section(s), skipped {len(skipped_ids)}. Used {len(selected_ids)} source(s).",
                },
            })

        except Exception as exc:
            logger.exception("pp_stream_unexpected_error", error=str(exc))
            yield emit("result", {
                "status": "failed", "run_id": run_id, "mode": inp.mode,
                "message": f"Unexpected error during run: {str(exc)[:200]}",
            })

    return StreamingResponse(event_stream(), media_type="text/event-stream")
