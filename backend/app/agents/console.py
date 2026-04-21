"""
Workspace console orchestration.

Bounded workflow: interpret → resolve → plan → execute → summarize.
Each step is deterministic with clear stop conditions.
"""
from __future__ import annotations

import structlog
from typing import AsyncGenerator

from app.agents.console_intent import classify_console_intent, console_intent_to_scope_label

logger = structlog.get_logger()


async def _get_llm(guest_id: str):
    from app.llm import LLMClient, resolve_llm_settings_for_guest
    resolved = await resolve_llm_settings_for_guest(guest_id)
    return LLMClient(resolved)


async def _stream_llm_answer(
    llm,
    system: str,
    question: str,
    intent: str,
    scope_label: str,
) -> AsyncGenerator[dict, None]:
    """Stream LLM response as token messages, then emit answer_done."""
    full_text = ""
    try:
        async for token in llm.stream_text(
            system=system,
            messages=[{"role": "user", "content": question}],
            max_tokens=2500,
            temperature=0.3,
        ):
            full_text += token
            yield {"type": "token", "content": token}
    except Exception as exc:
        logger.exception("console_stream_failed", error=str(exc))
        if full_text:
            yield {"type": "answer_json", "content": _answer_json(full_text, intent, scope_label)}
            yield {"type": "answer_done", "content": ""}
        else:
            yield {"type": "error", "content": f"Failed to generate response: {str(exc)}"}
        return

    if not full_text.strip():
        full_text = "I wasn't able to generate a response. Could you rephrase your question?"

    yield {"type": "answer_json", "content": _answer_json(full_text, intent, scope_label)}
    yield {"type": "answer_done", "content": ""}


def _answer_json(text: str, intent: str, scope_label: str) -> dict:
    return {
        "direct_answer": text,
        "key_points": None,
        "evidence": [],
        "plain_language": None,
        "bigger_picture": None,
        "uncertainty": None,
        "answer_mode": intent,
        "scope_label": scope_label,
    }


async def run_console_turn(
    session_id: str,
    workspace_id: str,
    question: str,
    guest_id: str = "",
    context: dict | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Run one workspace console turn. Yields WebSocket message dicts.
    """
    ctx = context or {}
    has_active_paper = bool(ctx.get("active_paper_id"))
    has_active_deliverable = bool(ctx.get("active_deliverable_id"))
    has_focused_section = bool(ctx.get("focused_section_id"))

    intent, confidence = classify_console_intent(
        question,
        has_active_paper=has_active_paper,
        has_active_deliverable=has_active_deliverable,
        has_focused_section=has_focused_section,
    )

    scope_label = console_intent_to_scope_label(intent)
    yield {
        "type": "mode_info",
        "content": {
            "answer_mode": intent,
            "scope_label": scope_label,
        },
    }

    logger.info(
        "console_intent_classified",
        session_id=session_id,
        workspace_id=workspace_id,
        intent=intent,
        confidence=confidence,
        question=question[:80],
    )

    # Route: paper_question → delegate to existing paper agent
    if intent == "paper_question" and has_active_paper:
        yield {"type": "status", "content": "Routing to paper agent…"}
        from app.agents.graph import run_agent_turn
        async for msg in run_agent_turn(
            session_id,
            question,
            question_id=None,
            guest_id=guest_id,
        ):
            yield msg
        return

    # All other intents use LLM with intent-specific system prompts
    try:
        llm = await _get_llm(guest_id)
    except Exception as exc:
        logger.exception("console_llm_init_failed", error=str(exc))
        yield {"type": "error", "content": f"LLM not configured: {str(exc)}"}
        return

    system_prompt = _build_system_prompt(intent, ctx)
    yield {"type": "status", "content": _status_for_intent(intent)}

    async for msg in _stream_llm_answer(llm, system_prompt, question, intent, scope_label):
        yield msg


def _status_for_intent(intent: str) -> str:
    return {
        "discover_sources": "Searching for relevant sources…",
        "compare_sources": "Analyzing and comparing…",
        "deliverable_edit": "Working on your deliverable…",
        "deliverable_draft": "Drafting content…",
        "navigation_or_open": "Processing your request…",
        "agenda_followup": "Checking your agenda…",
        "workspace_question": "Thinking about your research topic…",
    }.get(intent, "Thinking…")


def _build_system_prompt(intent: str, ctx: dict) -> str:
    sources = ctx.get("included_sources", [])
    source_context = ""
    if sources:
        source_lines = []
        for s in sources[:15]:
            line = f"- {s.get('title', 'Untitled')}"
            if s.get('authors'):
                authors = s['authors'][:3]
                line += f" ({', '.join(authors)})"
            if s.get('year'):
                line += f" [{s['year']}]"
            source_lines.append(line)
        source_context = f"\n\nUser's workspace sources ({len(sources)} included):\n" + "\n".join(source_lines)

    base = (
        "You are a research assistant in PaperPilot, helping a scholar with their research workspace. "
        "Be concise, actionable, and grounded in research methodology. "
        "Use markdown formatting: headings, bullet lists, numbered lists, bold for emphasis. "
        "Always provide a substantive answer. Never ask for clarification unless the question is truly ambiguous. "
        "If the user's request is broad, make reasonable assumptions and provide useful content directly."
        f"{source_context}"
    )

    if intent == "discover_sources":
        return (
            f"{base}\n\n"
            "The user wants to find relevant academic sources. Provide:\n"
            "1. A curated list of 5-8 specific papers/topics they should look for, with brief relevance explanations\n"
            "2. Concrete search queries they can use (formatted as bullet points)\n"
            "3. Key authors or research groups in this area\n"
            "4. Suggested venues (conferences, journals)\n\n"
            "Be specific and actionable. Don't ask what their topic is — infer it from their question and workspace context."
        )

    if intent == "compare_sources":
        return (
            f"{base}\n\n"
            "The user wants to compare or contrast research papers, methods, or approaches. Help them by:\n"
            "1. Identifying key dimensions for comparison (methodology, scale, results, assumptions)\n"
            "2. Structuring the comparison clearly (use tables or bullet points)\n"
            "3. Highlighting trade-offs and complementary strengths\n"
            "4. Suggesting which approach might be better for specific use cases\n\n"
            "If you don't have specific paper details, ask clarifying questions about what they want to compare."
        )

    if intent in ("deliverable_edit", "deliverable_draft"):
        section_info = ""
        if ctx.get("focused_section_id"):
            section_info = f"\nThe user has a section focused (ID: {ctx['focused_section_id']})."
        deliverable_info = ""
        if ctx.get("deliverables"):
            deliverable_info = "\n\nCurrent deliverables in workspace:"
            for d in ctx["deliverables"]:
                deliverable_info += f"\n- {d.get('title', 'Untitled')} ({d.get('type', '')})"
                for s in d.get("sections", []):
                    deliverable_info += f"\n  • {s.get('title', '')} [{s.get('status', 'empty')}]"
        return (
            f"{base}\n\n"
            "The user wants help with their research deliverable (e.g., literature review, proposal, report).{section_info}\n"
            f"{deliverable_info}\n"
            "Help them by:\n"
            "1. Providing concrete writing suggestions or improvements\n"
            "2. Suggesting structure and organization\n"
            "3. Offering academic phrasing and transitions\n"
            "4. Identifying gaps in argumentation\n\n"
            "Write in an academic but clear style. Suggest specific text they can use directly.\n"
            "Remind them they can use the Deliverable panel's AI Draft feature for full section generation."
        )

    if intent == "navigation_or_open":
        return (
            f"{base}\n\n"
            "The user wants to navigate or find something in the workspace. Help them by explaining:\n"
            "- Papers: visible in the left sidebar library panel\n"
            "- Sources: click 'Sources' tab in the viewer panel\n"
            "- Deliverables: click 'Deliverable' tab in the viewer panel\n"
            "- Deep Research: use the 'Deep Research' nav item in the left sidebar\n"
            "- Proposal Plan: use the 'Proposal' nav item in the left sidebar\n"
            "- Agenda: click 'Agenda' tab in the viewer panel\n"
            "- Concept Map: click 'Concepts' tab in the viewer panel\n"
            "- Settings: gear icon at the bottom of the left sidebar\n\n"
            "Be brief and direct. If they want to open a specific paper, tell them to click it in the library."
        )

    if intent == "agenda_followup":
        return (
            f"{base}\n\n"
            "The user is asking about their research agenda or next steps. Help them by:\n"
            "1. Suggesting concrete next actions for their research\n"
            "2. Prioritizing tasks based on research workflow\n"
            "3. Identifying dependencies between tasks\n\n"
            "The Agenda panel tracks their reading progress and next questions for each paper."
        )

    # workspace_question (default)
    deliverable_info = ""
    if ctx.get("deliverables"):
        deliverable_info = "\n\nCurrent deliverables in workspace:"
        for d in ctx["deliverables"]:
            deliverable_info += f"\n- {d.get('title', 'Untitled')} ({d.get('type', '')})"
            for s in d.get("sections", []):
                deliverable_info += f"\n  • {s.get('title', '')} [{s.get('status', 'empty')}]"
    return (
        f"{base}\n\n"
        "Answer their research question directly and substantively. Use:\n"
        "- Structured markdown (headings, lists, bold) for readability\n"
        "- Specific examples and concrete suggestions\n"
        "- References to their workspace sources when relevant\n"
        f"- Actionable next steps{deliverable_info}"
    )
