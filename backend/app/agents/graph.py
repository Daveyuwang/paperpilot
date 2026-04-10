"""
LangGraph agent graph runner.
Exposes run_agent_turn() as an async generator yielding WebSocket message dicts.

Node pipeline:
  route_input → enrich_query → [branch by intent]
  paper_understanding: hybrid_retrieve → fetch_external → extract_evidence → synthesize_answer (streaming)
  concept_explanation: hybrid_retrieve → synthesize_concept_explanation (streaming)
  external_expansion:  synthesize_expansion
  → update_session → suggest_next
"""
from __future__ import annotations
import asyncio
import time
from typing import AsyncGenerator
import structlog

from app.agents.state import AgentState
from app.agents.nodes import (
    route_input,
    enrich_query,
    hybrid_retrieve,
    fetch_external,
    extract_evidence,
    synthesize_answer,
    synthesize_concept_explanation,
    synthesize_expansion,
    synthesize_navigation,
    explain_term,
    update_session,
    suggest_next,
)
from app.agents.intent import intent_to_scope_label
from app.db.redis_client import get_session_state

logger = structlog.get_logger()


async def run_agent_turn(
    session_id: str,
    question: str,
    question_id: str | None = None,
    mode_override: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Run one full agent turn and yield WebSocket message dicts.
    Types: mode_info | status | token | answer_json | evidence_ready | chunk_refs | answer_done | next_question | suggested_questions | error
    """
    t_start = time.perf_counter()
    t_after_router: float = 0.0
    t_after_retrieval: float = 0.0
    t_after_synthesis: float = 0.0
    state = None
    paper_id: str | None = None
    completed = False

    try:
        paper_id = await _get_paper_id(session_id)
        if not paper_id:
            yield {"type": "error", "content": "Session not found or has no paper."}
            return

        state = await _load_state(session_id, paper_id, question, question_id, mode_override=mode_override)

        # ── Route + intent classification ─────────────────────────────────
        logger.info("question_submit", session_id=session_id, question=question[:80])
        yield {"type": "status", "content": "Received"}
        state = state.model_copy(update=await route_input(state))
        t_after_router = time.perf_counter()

        # Emit mode_info immediately so frontend can configure status steps and scope badge
        scope_label = intent_to_scope_label(state.intent)
        yield {
            "type": "mode_info",
            "content": {
                "answer_mode": state.intent,
                "scope_label": scope_label,
            },
        }
        yield {"type": "status", "content": "Understanding request…"}

        # ── Internal query enrichment ──────────────────────────────────────
        state = state.model_copy(update=await enrich_query(state))

        if state.intent == "concept_explanation":
            # ── Concept explanation: retrieve paper context, then explain ──
            logger.info("mode_selected", mode="concept_explanation", session_id=session_id)
            yield {"type": "status", "content": "Interpreting concept…"}

            logger.info("retrieval_start", session_id=session_id, mode="concept_explanation")
            state = state.model_copy(update=await hybrid_retrieve(state))
            t_after_retrieval = time.perf_counter()
            logger.info("retrieval_end", session_id=session_id, chunk_count=len(state.retrieved_chunks))

            # Check if paper has relevant context for this concept
            if state.retrieved_chunks:
                yield {"type": "status", "content": "Linking to paper context…"}

            msg_queue: asyncio.Queue[dict | None] = asyncio.Queue()

            async def on_concept_msg(msg: dict):
                await msg_queue.put(msg)

            logger.info("generation_start", session_id=session_id, mode="concept_explanation")
            concept_task = asyncio.create_task(
                _run_concept(state, on_concept_msg, msg_queue)
            )
            while True:
                msg = await msg_queue.get()
                if msg is None:
                    break
                yield msg

            state = state.model_copy(update=await concept_task)
            t_after_synthesis = time.perf_counter()
            logger.info("generation_end", session_id=session_id, mode="concept_explanation")

            # Emit citations if any were found in retrieved chunks
            if state.citations:
                yield {"type": "chunk_refs", "content": state.citations}

        elif state.intent in ("external_expansion", "expansion"):
            # ── External expansion: answer from general knowledge ──
            logger.info("mode_selected", mode="external_expansion", session_id=session_id)
            yield {"type": "status", "content": "Searching the web…"}

            msg_queue2: asyncio.Queue[dict | None] = asyncio.Queue()

            async def on_exp_msg(msg: dict):
                await msg_queue2.put(msg)

            exp_task = asyncio.create_task(
                _run_expansion(state, on_exp_msg, msg_queue2)
            )
            while True:
                msg = await msg_queue2.get()
                if msg is None:
                    break
                yield msg

            state = state.model_copy(update=await exp_task)
            t_after_synthesis = time.perf_counter()

        elif state.intent == "navigation_or_next_step":
            # ── Navigation: lightweight guidance on what to read/explore next ──
            logger.info("mode_selected", mode="navigation_or_next_step", session_id=session_id)
            yield {"type": "status", "content": "Mapping next steps…"}

            msg_queue_nav: asyncio.Queue[dict | None] = asyncio.Queue()

            async def on_nav_msg(msg: dict):
                await msg_queue_nav.put(msg)

            nav_task = asyncio.create_task(
                _run_navigation(state, on_nav_msg, msg_queue_nav)
            )
            while True:
                msg = await msg_queue_nav.get()
                if msg is None:
                    break
                yield msg

            state = state.model_copy(update=await nav_task)
            t_after_synthesis = time.perf_counter()

        else:
            # ── Paper understanding (default) ─────────────────────────────
            logger.info("mode_selected", mode="paper_understanding", session_id=session_id)

            # Retrieve
            yield {"type": "status", "content": "Retrieving passages from paper…"}
            logger.info("retrieval_start", session_id=session_id)
            state = state.model_copy(update=await hybrid_retrieve(state))
            t_after_retrieval = time.perf_counter()
            logger.info("retrieval_end", session_id=session_id, chunk_count=len(state.retrieved_chunks))

            # External knowledge (conditional)
            state = state.model_copy(update=await fetch_external(state))

            # Extract evidence
            state = state.model_copy(update=await extract_evidence(state))
            yield {
                "type": "evidence_ready",
                "content": {
                    "confidence": state.evidence_confidence,
                    "evidence_count": len(state.extracted_evidence),
                    "coverage_gap": state.coverage_gap,
                },
            }

            # Synthesize (streaming — first token triggers "Writing grounded answer…" status)
            msg_queue3: asyncio.Queue[dict | None] = asyncio.Queue()

            async def on_synth_msg(msg: dict):
                await msg_queue3.put(msg)

            logger.info("generation_start", session_id=session_id, mode="paper_understanding")
            synthesis_task = asyncio.create_task(
                _run_synthesis(state, on_synth_msg, msg_queue3)
            )
            while True:
                msg = await msg_queue3.get()
                if msg is None:
                    break
                yield msg

            state = state.model_copy(update=await synthesis_task)
            t_after_synthesis = time.perf_counter()
            logger.info("generation_end", session_id=session_id, mode="paper_understanding")

            # Explain jargon (optional)
            if _has_unexplained_jargon(state.answer_text, state.explained_terms):
                update = await explain_term(state)
                if update:
                    state = state.model_copy(update=update)

            # Emit citations
            if state.citations:
                yield {"type": "chunk_refs", "content": state.citations}

        # ── Update session ─────────────────────────────────────────────────
        state = state.model_copy(update=await update_session(state))

        # ── Suggest next ───────────────────────────────────────────────────
        state = state.model_copy(update=await suggest_next(state))

        completed = True
        yield {"type": "answer_done", "content": state.answer_json or state.answer_text}

        if state.next_question:
            yield {"type": "next_question", "content": state.next_question}

        if state.suggested_questions:
            yield {"type": "suggested_questions", "content": state.suggested_questions}

    except Exception as exc:
        logger.exception("agent_turn_failed", session_id=session_id, error=str(exc))
        yield {"type": "error", "content": str(exc)}

    finally:
        _log_request_trace(
            state=state,
            paper_id=paper_id or "",
            question=question,
            session_id=session_id,
            t_start=t_start,
            t_after_router=t_after_router,
            t_after_retrieval=t_after_retrieval,
            t_after_synthesis=t_after_synthesis,
            completed=completed,
        )


# ── Helpers ────────────────────────────────────────────────────────────────

async def _run_synthesis(state: AgentState, callback, queue: asyncio.Queue) -> dict:
    try:
        return await synthesize_answer(state, stream_callback=callback)
    finally:
        await queue.put(None)


async def _run_concept(state: AgentState, callback, queue: asyncio.Queue) -> dict:
    try:
        return await synthesize_concept_explanation(state, stream_callback=callback)
    finally:
        await queue.put(None)


async def _run_expansion(state: AgentState, callback, queue: asyncio.Queue) -> dict:
    try:
        return await synthesize_expansion(state, stream_callback=callback)
    finally:
        await queue.put(None)


async def _run_navigation(state: AgentState, callback, queue: asyncio.Queue) -> dict:
    try:
        return await synthesize_navigation(state, stream_callback=callback)
    finally:
        await queue.put(None)


def _log_request_trace(
    *,
    state,
    paper_id: str,
    question: str,
    session_id: str,
    t_start: float,
    t_after_router: float,
    t_after_retrieval: float,
    t_after_synthesis: float,
    completed: bool,
) -> None:
    """Emit a single structured request_trace log entry at the end of every turn."""
    t_end = time.perf_counter()
    if state is None:
        # Failed before state was loaded — minimal trace
        logger.info(
            "request_trace",
            user_query=question[:200],
            conversation_id=session_id,
            paper_id=paper_id,
            cancelled=not completed,
            latency_total_ms=round((t_end - t_start) * 1000),
        )
        return

    meta: dict = state.trace_metadata or {}
    answer_json: dict = state.answer_json or {}
    scope_label_final = answer_json.get("scope_label", "")
    answer_mode_used = answer_json.get("answer_mode", state.intent)

    # Detect response language from answer text (CJK heuristic)
    answer_text = state.answer_text or ""
    cjk = sum(1 for ch in answer_text if "\u4e00" <= ch <= "\u9fff")
    response_language = state.session_language or ("zh" if cjk > 5 else "en")

    logger.info(
        "request_trace",
        # Identity
        user_query=question[:200],
        conversation_id=session_id,
        paper_id=paper_id,
        # Routing
        predicted_mode=state.intent,
        router_confidence=round(state.router_confidence, 3),
        mode_override_used=bool(state.mode_override),
        # Output
        scope_label_final=scope_label_final,
        answer_mode_used=answer_mode_used,
        response_language=response_language,
        # Tool / web search (expansion mode only)
        tool_requested=meta.get("tool_requested", False),
        tool_called=meta.get("tool_called", False),
        tool_name=meta.get("tool_name"),
        tool_success=meta.get("tool_success", False),
        search_results_count=meta.get("search_results_count", 0),
        # Fallback
        fallback_triggered=meta.get("fallback_triggered", False),
        fallback_reason=meta.get("fallback_reason"),
        # Latency (ms)
        latency_router_ms=round((t_after_router - t_start) * 1000) if t_after_router else None,
        latency_retrieval_ms=round((t_after_retrieval - t_after_router) * 1000) if t_after_retrieval and t_after_router else None,
        latency_synthesis_ms=round((t_after_synthesis - max(t_after_retrieval, t_after_router)) * 1000) if t_after_synthesis else None,
        latency_tool_ms=meta.get("latency_tool_ms"),
        latency_total_ms=round((t_end - t_start) * 1000),
        # Outcome
        cancelled=not completed,
    )


def _has_unexplained_jargon(text: str, already_explained: list[str]) -> bool:
    import re
    acronyms = {a for a in re.findall(r"\b([A-Z]{2,6})\b", text)}
    common = {"Q", "A", "In", "For", "The", "This", "We"}
    new = acronyms - common - {t.upper() for t in already_explained}
    return len(new) >= 2


async def _load_state(
    session_id: str,
    paper_id: str,
    question: str,
    question_id: str | None,
    mode_override: str | None = None,
) -> AgentState:
    redis_state = await get_session_state(session_id)
    paper_title, paper_abstract, guide_questions = await _load_paper_context(paper_id)
    return AgentState(
        session_id=session_id,
        paper_id=paper_id,
        question=question,
        question_id=question_id,
        mode_override=mode_override,
        paper_title=paper_title,
        paper_abstract=paper_abstract,
        guide_questions=guide_questions,
        covered_question_ids=redis_state.get("covered_question_ids", []),
        covered_stages=redis_state.get("covered_stages", []),
        explained_terms=redis_state.get("explained_terms", []),
        session_summary=redis_state.get("session_summary", ""),
        turn_count=redis_state.get("turn_count", 0),
        suggested_questions=redis_state.get("suggested_questions", []),
        recent_messages=redis_state.get("recent_messages", []),
        session_language=redis_state.get("session_language", ""),
    )


async def _load_paper_context(paper_id: str) -> tuple[str, str, list[dict]]:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select
    from app.models.orm import Paper, GuideQuestion
    from app.config import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        paper = await db.get(Paper, paper_id)
        if not paper:
            return "", "", []
        result = await db.execute(
            select(GuideQuestion)
            .where(GuideQuestion.paper_id == paper_id)
            .order_by(GuideQuestion.order_index)
        )
        questions = [
            {
                "id": q.id,
                "question": q.question,
                "stage": q.stage.value,
                "anchor_sections": q.anchor_sections or [],
            }
            for q in result.scalars()
        ]
    return paper.title or "", paper.abstract or "", questions


async def _get_paper_id(session_id: str) -> str | None:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.models.orm import Session
    from app.config import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        session = await db.get(Session, session_id)
        return session.paper_id if session else None
