"""
LangGraph agent nodes.
Each node is a pure async function: takes AgentState, returns a state update dict.

Pipeline order:
  route_input → enrich_query → [branch by intent]
  paper_understanding: hybrid_retrieve → fetch_external → extract_evidence → synthesize_answer (streaming)
  concept_explanation: hybrid_retrieve → synthesize_concept_explanation (streaming)
  external_expansion:  synthesize_expansion
  → explain_term → update_session → suggest_next
"""
from __future__ import annotations
import json
import re
import structlog
from anthropic import AsyncAnthropic

from app.config import get_settings
from app.agents.state import AgentState
from app.agents.prompts import (
    EVIDENCE_EXTRACTION_SYSTEM,
    SYNTHESIZE_SYSTEM,
    SYNTHESIZE_USER_TEMPLATE,
    CONCEPT_EXPLANATION_SYSTEM,
    CONCEPT_EXPLANATION_USER_TEMPLATE,
    EXPANSION_SYNTHESIZE_SYSTEM,
    EXPANSION_WITH_SEARCH_SYSTEM,
    NAVIGATION_SYNTHESIZE_SYSTEM,
    NAVIGATION_SYNTHESIZE_USER_TEMPLATE,
    QUERY_ENRICHMENT_SYSTEM,
    QUERY_ENRICHMENT_USER_TEMPLATE,
    EXPLAIN_TERM_SYSTEM,
    EXTERNAL_DECISION_SYSTEM,
    SESSION_COMPRESS_PROMPT,
)

logger = structlog.get_logger()
settings = get_settings()

# Regex to detect Python byte-string repr leaked into text (e.g. b'\xc3\xa9')
_BYTE_STR_RE = re.compile(r"b['\"].*?['\"]")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _sanitize_text(text: str | None, fallback: str = "") -> str:
    """Strip non-printable chars and byte-string artifacts from text."""
    if not text:
        return fallback
    if not isinstance(text, str):
        try:
            text = str(text, "utf-8", errors="replace")
        except Exception:
            text = str(text)
    text = _BYTE_STR_RE.sub("", text)
    text = _CONTROL_CHAR_RE.sub("", text)
    text = text.strip()
    return text if text else fallback


def _client() -> AsyncAnthropic:
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


def _clean_json(raw: str) -> str:
    """Strip markdown fences and leading/trailing whitespace from JSON responses."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


# ── Streaming direct_answer extractor ─────────────────────────────────────

class _DirectAnswerExtractor:
    """
    Stateful extractor that parses a streaming JSON response and emits the
    `direct_answer` field value as individual token events as they arrive.

    The LLM is instructed to output `direct_answer` as the FIRST field, so
    tokens for it arrive early in the stream.
    """

    def __init__(self):
        self._buf = ""
        self._state = "scanning"  # scanning | found_key | found_colon | in_value | done
        self._emitted = ""

    def feed(self, chunk: str) -> list[str]:
        """
        Feed a new chunk. Returns a list of token strings to emit.
        """
        if self._state == "done":
            self._buf += chunk
            return []

        self._buf += chunk
        tokens: list[str] = []

        while True:
            if self._state == "scanning":
                idx = self._buf.find('"direct_answer"')
                if idx == -1:
                    # Keep tail in case key spans chunks
                    self._buf = self._buf[-20:]
                    break
                self._buf = self._buf[idx + len('"direct_answer"'):]
                self._state = "found_key"

            elif self._state == "found_key":
                colon_idx = self._buf.find(":")
                if colon_idx == -1:
                    break
                self._buf = self._buf[colon_idx + 1:]
                self._state = "found_colon"

            elif self._state == "found_colon":
                # Skip whitespace to find the opening quote
                stripped = self._buf.lstrip()
                if not stripped:
                    self._buf = ""
                    break
                if stripped[0] != '"':
                    # Not a string value — skip
                    self._state = "done"
                    break
                # Opening quote found
                self._buf = stripped[1:]  # consume it
                self._state = "in_value"

            elif self._state == "in_value":
                # Scan for closing quote (not escaped)
                escaped = False
                end_idx = -1
                for i, ch in enumerate(self._buf):
                    if escaped:
                        escaped = False
                        continue
                    if ch == "\\":
                        escaped = True
                        continue
                    if ch == '"':
                        end_idx = i
                        break

                if end_idx == -1:
                    # Closing quote not found yet — emit everything as tokens
                    to_emit = self._buf
                    self._buf = ""
                    if to_emit:
                        tokens.append(to_emit)
                        self._emitted += to_emit
                    break
                else:
                    # Closing quote found — emit up to it
                    to_emit = self._buf[:end_idx]
                    if to_emit:
                        tokens.append(to_emit)
                        self._emitted += to_emit
                    self._buf = self._buf[end_idx + 1:]
                    self._state = "done"
                    break

        return tokens

    def get_buffer(self) -> str:
        return self._buf

    def is_done(self) -> bool:
        return self._state == "done"


# ── Node 1: RouteInput ────────────────────────────────────────────────────

async def route_input(state: AgentState) -> dict:
    from app.agents.intent import classify_intent, intent_to_scope_label

    update: dict = {}

    if state.question_id:
        gq = next((q for q in state.guide_questions if q["id"] == state.question_id), None)
        if gq:
            update = {"input_type": "guided", "anchor_sections": gq.get("anchor_sections", [])}
        else:
            update = {"input_type": "free", "anchor_sections": []}
    else:
        update = {"input_type": "free", "anchor_sections": []}

    # Respect explicit mode override from client (skips intent classification)
    if state.mode_override:
        intent = state.mode_override
        confidence = 1.0
        logger.info("mode_override_applied", intent=intent, question=state.question[:80])
    else:
        intent, confidence = await classify_intent(state.question, state.paper_title)
        logger.info("intent_classified", intent=intent, confidence=confidence, question=state.question[:80])

    update["intent"] = intent
    update["answer_mode"] = intent
    update["router_confidence"] = confidence

    return update


# ── Node 1b: EnrichQuery ─────────────────────────────────────────────────

async def enrich_query(state: AgentState) -> dict:
    """
    Internal query enrichment for better retrieval.
    Combines question + paper context + session state.
    The enriched query is NEVER exposed to the user.
    """
    # If it's a guided question with anchor sections, basic enrichment suffices
    if state.input_type == "guided" and state.anchor_sections:
        return {"enriched_query": state.question}

    covered_terms = ", ".join(state.explained_terms[-5:]) if state.explained_terms else "none"
    user_content = QUERY_ENRICHMENT_USER_TEMPLATE.format(
        title=state.paper_title or "this paper",
        abstract_snippet=(state.paper_abstract or "")[:300],
        session_summary=state.session_summary or "First turn.",
        covered_terms=covered_terms,
        question=state.question,
    )

    try:
        message = await _client().messages.create(
            model=settings.claude_model,
            max_tokens=150,
            system=QUERY_ENRICHMENT_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        enriched = message.content[0].text.strip()
        logger.debug("query_enriched", original=state.question[:60], enriched=enriched[:80])
        return {"enriched_query": enriched or state.question}
    except Exception as exc:
        logger.warning("query_enrichment_failed", error=str(exc))
        return {"enriched_query": state.question}


# ── Node 2: HybridRetrieve ────────────────────────────────────────────────

async def hybrid_retrieve(state: AgentState) -> dict:
    from app.retrieval.hybrid import hybrid_retrieve as _retrieve

    # Use enriched query if available, else fall back to raw question
    query = state.enriched_query or state.question

    chunks = await _retrieve(
        query=query,
        paper_id=state.paper_id,
        anchor_sections=state.anchor_sections,
        top_k=6,
    )
    chunks = await _fill_missing_content(chunks, state.paper_id)
    return {"retrieved_chunks": chunks}


async def _fill_missing_content(chunks: list[dict], paper_id: str) -> list[dict]:
    missing_ids = [c["chunk_id"] for c in chunks if not c.get("content")]
    if not missing_ids:
        return chunks

    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select
    from app.models.orm import Chunk

    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        result = await db.execute(select(Chunk).where(Chunk.id.in_(missing_ids)))
        chunk_map = {c.id: c for c in result.scalars()}

    for item in chunks:
        if not item.get("content") and item["chunk_id"] in chunk_map:
            c = chunk_map[item["chunk_id"]]
            item["content"] = c.content
            item["section_title"] = c.section_title
            item["page_number"] = c.page_number
            item["bbox"] = c.bbox
    return chunks


# ── Node 3: FetchExternal ─────────────────────────────────────────────────

async def fetch_external(state: AgentState) -> dict:
    """
    Only fetch external knowledge when in-paper evidence is clearly insufficient.
    """
    if not state.retrieved_chunks:
        context = await _fetch_from_sources(state.question, state.paper_title)
        return {"needs_external": bool(context), "external_context": context}

    evidence_preview = "\n".join(
        c.get("content", "")[:200] for c in state.retrieved_chunks[:3]
    )
    decision_msg = await _client().messages.create(
        model=settings.claude_model,
        max_tokens=150,
        system=EXTERNAL_DECISION_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Question: {state.question}\n\nEvidence preview:\n{evidence_preview}",
        }],
    )
    try:
        raw = _clean_json(decision_msg.content[0].text)
        decision = json.loads(raw)
        needs = decision.get("needs_external", False)
    except Exception:
        needs = False
        decision = {}

    if not needs:
        return {"needs_external": False, "external_context": ""}

    context = await _fetch_from_sources(
        decision.get("search_query", state.question), state.paper_title
    )
    return {"needs_external": bool(context), "external_context": context}


async def _fetch_from_sources(query: str, paper_title: str) -> str:
    import httpx
    parts = []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                "https://en.wikipedia.org/api/rest_v1/page/summary/" + query.replace(" ", "_")
            )
            if resp.status_code == 200:
                extract = resp.json().get("extract", "")
                if extract:
                    parts.append(f"Wikipedia: {extract[:700]}")
    except Exception as exc:
        logger.warning("wikipedia_fetch_failed", error=str(exc))
    return "\n\n".join(parts)


# ── Node 4: ExtractEvidence ───────────────────────────────────────────────

async def extract_evidence(state: AgentState) -> dict:
    """
    Evidence-first pass: before synthesis, identify which retrieved chunks
    actually contain relevant evidence.
    """
    if not state.retrieved_chunks:
        return {
            "extracted_evidence": [],
            "evidence_confidence": 0.0,
            "coverage_gap": "No chunks were retrieved for this question.",
        }

    chunks_text = ""
    for i, chunk in enumerate(state.retrieved_chunks, 1):
        section = chunk.get("section_title") or "?"
        page = chunk.get("page_number") or "?"
        content = (chunk.get("content") or "")[:700]
        chunks_text += f"\n[Chunk {i}] §{section}, p.{page}:\n{content}\n"

    message = await _client().messages.create(
        model=settings.claude_model,
        max_tokens=900,
        system=EVIDENCE_EXTRACTION_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Question: {state.question}\n\nChunks:{chunks_text}",
        }],
    )

    raw = _clean_json(message.content[0].text)

    try:
        data = json.loads(raw)
    except Exception:
        logger.warning("evidence_extraction_parse_failed", raw=raw[:200])
        return {
            "extracted_evidence": [],
            "evidence_confidence": 0.3,
            "coverage_gap": "Evidence extraction parsing failed; proceeding with raw chunks.",
        }

    raw_evidence = data.get("evidence", [])
    enriched = []
    for item in raw_evidence:
        idx = item.get("chunk_index", 1) - 1
        if 0 <= idx < len(state.retrieved_chunks):
            chunk = state.retrieved_chunks[idx]
            enriched.append({
                "type": item.get("type", "INFERRED"),
                "passage": _sanitize_text(item.get("passage", ""), "(citation)"),
                "note": _sanitize_text(item.get("note", "")),
                "chunk_id": chunk.get("chunk_id"),
                "section_title": _sanitize_text(chunk.get("section_title")),
                "page_number": chunk.get("page_number"),
                "bbox": chunk.get("bbox"),
            })

    return {
        "extracted_evidence": enriched,
        "evidence_confidence": float(data.get("confidence", 0.5)),
        "coverage_gap": data.get("coverage_gap", ""),
    }


# ── Node 5: SynthesizeAnswer (paper_understanding, streaming) ─────────────

async def synthesize_answer(state: AgentState, stream_callback=None) -> dict:
    """
    Generate a structured answer with streaming tokens for the direct_answer field.
    Emits token events as the direct_answer text streams in, then emits the full
    answer_json when complete.
    """
    conf = state.evidence_confidence
    if conf >= 0.8:
        confidence_label = "High"
    elif conf >= 0.6:
        confidence_label = "Moderate"
    elif conf >= 0.4:
        confidence_label = "Low"
    else:
        confidence_label = "Very low"

    evidence_lines = []
    citations = []
    for item in state.extracted_evidence:
        label = "EXPLICIT" if item["type"] == "EXPLICIT" else "INFERRED"
        section = item.get("section_title") or "?"
        page = item.get("page_number") or "?"
        passage = item.get("passage", "")
        note = item.get("note", "")
        evidence_lines.append(
            f"[{label}] §{section}, p.{page}\nPassage: {passage}\nNote: {note}"
        )
        if item.get("chunk_id"):
            citations.append({
                "chunk_id": item["chunk_id"],
                "section_title": item.get("section_title"),
                "page_number": item.get("page_number"),
                "bbox": item.get("bbox"),
            })

    if not evidence_lines and state.retrieved_chunks:
        for chunk in state.retrieved_chunks[:3]:
            section = chunk.get("section_title") or "?"
            page = chunk.get("page_number") or "?"
            evidence_lines.append(
                f"[RAW] §{section}, p.{page}\n{(chunk.get('content') or '')[:400]}"
            )
            if chunk.get("chunk_id"):
                citations.append({
                    "chunk_id": chunk["chunk_id"],
                    "section_title": chunk.get("section_title"),
                    "page_number": chunk.get("page_number"),
                    "bbox": chunk.get("bbox"),
                })

    evidence_block = "\n\n".join(evidence_lines) if evidence_lines else "No relevant evidence retrieved."
    coverage_gap_block = f"Coverage gap: {state.coverage_gap}" if state.coverage_gap else ""
    external_block = f"External background:\n{state.external_context}" if state.external_context else ""

    user_content = _language_note(state.session_language) + SYNTHESIZE_USER_TEMPLATE.format(
        title=state.paper_title or "this paper",
        session_summary=_build_session_context(state),
        question=state.question,
        confidence_label=confidence_label,
        confidence=conf,
        coverage_gap_block=coverage_gap_block,
        evidence_block=evidence_block,
        external_block=external_block,
    )

    # Stream the response, extracting direct_answer tokens as they arrive
    accumulated = ""
    extractor = _DirectAnswerExtractor()
    first_token_emitted = False

    logger.info("generation_start", session_id=state.session_id, mode="paper_understanding")

    async with _client().messages.stream(
        model=settings.claude_model,
        max_tokens=1400,
        system=SYNTHESIZE_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        async for chunk in stream.text_stream:
            accumulated += chunk
            tokens = extractor.feed(chunk)
            for tok in tokens:
                if tok and stream_callback:
                    if not first_token_emitted:
                        # Signal that actual writing has started
                        await stream_callback({"type": "status", "content": "Writing grounded answer…"})
                        first_token_emitted = True
                    await stream_callback({"type": "token", "content": tok})

    logger.info("generation_end", session_id=state.session_id, mode="paper_understanding")

    raw = _clean_json(accumulated)
    try:
        answer_json = json.loads(raw)
    except Exception:
        logger.warning("synthesize_parse_failed", raw=raw[:300])
        answer_json = {
            "direct_answer": raw[:600],
            "evidence": [],
            "plain_language": None,
            "bigger_picture": None,
            "uncertainty": "Answer format could not be parsed.",
            "answer_mode": "paper_understanding",
            "scope_label": "Using this paper",
            "can_expand": True,
        }

    answer_json.setdefault("answer_mode", "paper_understanding")
    answer_json.setdefault("scope_label", "Using this paper")
    answer_json.setdefault("can_expand", True)

    full_answer = answer_json.get("direct_answer", "")

    if stream_callback:
        await stream_callback({"type": "answer_json", "content": answer_json})

    return {"answer_text": full_answer, "answer_json": answer_json, "citations": citations}


# ── Node 5b: SynthesizeConceptExplanation ────────────────────────────────

async def synthesize_concept_explanation(state: AgentState, stream_callback=None) -> dict:
    """
    Generate a concept explanation with general definition + paper context.
    Also uses streaming for the direct_answer.
    """
    evidence_block = "No paper context retrieved."
    citations = []
    if state.retrieved_chunks:
        lines = []
        for i, chunk in enumerate(state.retrieved_chunks[:4], 1):
            section = chunk.get("section_title") or "?"
            page = chunk.get("page_number") or "?"
            content = (chunk.get("content") or "")[:500]
            lines.append(f"[Chunk {i}] §{section}, p.{page}:\n{content}")
            if chunk.get("chunk_id"):
                citations.append({
                    "chunk_id": chunk["chunk_id"],
                    "section_title": chunk.get("section_title"),
                    "page_number": chunk.get("page_number"),
                    "bbox": chunk.get("bbox"),
                })
        evidence_block = "\n\n".join(lines)

    user_content = _language_note(state.session_language) + CONCEPT_EXPLANATION_USER_TEMPLATE.format(
        title=state.paper_title or "this paper",
        session_summary=_build_session_context(state),
        question=state.question,
        evidence_block=evidence_block,
    )

    accumulated = ""
    extractor = _DirectAnswerExtractor()
    first_token_emitted = False

    logger.info("generation_start", session_id=state.session_id, mode="concept_explanation")

    async with _client().messages.stream(
        model=settings.claude_model,
        max_tokens=1000,
        system=CONCEPT_EXPLANATION_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        async for chunk in stream.text_stream:
            accumulated += chunk
            tokens = extractor.feed(chunk)
            for tok in tokens:
                if tok and stream_callback:
                    if not first_token_emitted:
                        await stream_callback({"type": "status", "content": "Writing explanation…"})
                        first_token_emitted = True
                    await stream_callback({"type": "token", "content": tok})

    logger.info("generation_end", session_id=state.session_id, mode="concept_explanation")

    raw = _clean_json(accumulated)
    try:
        answer_json = json.loads(raw)
    except Exception:
        logger.warning("concept_explanation_parse_failed", raw=raw[:300])
        answer_json = {
            "direct_answer": raw[:600],
            "evidence": [],
            "paper_context": None,
            "plain_language": None,
            "bigger_picture": None,
            "uncertainty": None,
            "answer_mode": "concept_explanation",
            "scope_label": "General explanation with paper context",
            "can_expand": True,
        }

    answer_json.setdefault("answer_mode", "concept_explanation")
    answer_json.setdefault("scope_label", "General explanation with paper context")
    answer_json.setdefault("can_expand", True)

    full_answer = answer_json.get("direct_answer", "")

    if stream_callback:
        await stream_callback({"type": "answer_json", "content": answer_json})

    return {"answer_text": full_answer, "answer_json": answer_json, "citations": citations}


# ── Expansion helpers ─────────────────────────────────────────────────────

def _extract_json_object(text: str) -> str:
    """Extract the outermost {...} from text that may have LLM preamble."""
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def _expansion_fallback_json(emitted: str, web_searched: bool, raw_text: str = "") -> dict:
    """
    Safe fallback dict when JSON parsing fails for expansion mode.
    Prefers the streaming-extracted direct_answer text; falls back to the first
    substantive paragraph from raw accumulated text (stripping markdown artifacts).
    """
    answer = emitted
    if not answer and raw_text:
        # Strip common markdown and take first ~400 chars as a readable answer
        stripped = re.sub(r"[#*`\[\]_>]+", " ", raw_text).strip()
        stripped = re.sub(r"\s{2,}", " ", stripped)
        answer = (stripped[:400] + "…") if len(stripped) > 400 else stripped
    return {
        "direct_answer": _sanitize_text(answer or "", "Unable to generate a broader response."),
        "key_points": None,
        "evidence": [],
        "paper_context": None,
        "plain_language": None,
        "bigger_picture": None,
        "uncertainty": "Response format could not be parsed." if not emitted else None,
        "answer_mode": "external_expansion",
        "scope_label": "Beyond this paper",
        "can_expand": False,
    }


# ── Node 5c: SynthesizeExpansion ─────────────────────────────────────────

async def synthesize_expansion(state: AgentState, stream_callback=None) -> dict:
    """
    Generate a response for external_expansion questions.
    When web_search_enabled=True, passes the web_search_20250305 server-side tool
    and iterates the raw event stream to detect search calls and emit accurate status.
    Falls back to LLM-knowledge-only on tool error.
    """
    from anthropic.types import RawContentBlockStartEvent, RawContentBlockDeltaEvent

    web_search_enabled = settings.web_search_enabled
    system_prompt = EXPANSION_WITH_SEARCH_SYSTEM if web_search_enabled else EXPANSION_SYNTHESIZE_SYSTEM
    tools = (
        [{"type": "web_search_20260209", "name": "web_search"}]
        if web_search_enabled else []
    )

    user_content = _language_note(state.session_language) + (
        f"Paper: {state.paper_title or 'this paper'}\n"
        f"Question: {state.question}\n"
        f"Session context: {_build_session_context(state)}"
    )

    accumulated = ""
    extractor = _DirectAnswerExtractor()
    first_token_emitted = False
    web_searched = False
    search_queries_made = 0
    fallback_triggered = False
    fallback_reason = ""
    t_tool_start: float | None = None
    t_tool_end: float | None = None

    logger.info(
        "generation_start",
        session_id=state.session_id,
        mode="external_expansion",
        web_search_enabled=web_search_enabled,
        tool_declared_in_request=web_search_enabled,
    )

    request_kwargs: dict = dict(
        model=settings.claude_model,
        max_tokens=2000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    if tools:
        request_kwargs["tools"] = tools

    try:
        async with _client().messages.stream(**request_kwargs) as stream:
            async for event in stream:
                if isinstance(event, RawContentBlockStartEvent):
                    cb = event.content_block
                    # Server-side tools surface as "server_tool_use" (not "tool_use")
                    if cb.type in ("tool_use", "server_tool_use") and getattr(cb, "name", "") == "web_search":
                        if t_tool_start is None:
                            import time as _time
                            t_tool_start = _time.perf_counter()
                        web_searched = True
                        search_queries_made += 1
                        if stream_callback:
                            await stream_callback({"type": "status", "content": "Searching the web…"})
                    elif cb.type == "text" and web_searched and not first_token_emitted:
                        import time as _time
                        t_tool_end = _time.perf_counter()
                        if stream_callback:
                            await stream_callback({"type": "status", "content": "Synthesizing results…"})

                elif isinstance(event, RawContentBlockDeltaEvent):
                    delta = event.delta
                    if delta.type == "text_delta":
                        chunk = delta.text
                        accumulated += chunk
                        tokens = extractor.feed(chunk)
                        for tok in tokens:
                            if tok and stream_callback:
                                if not first_token_emitted:
                                    if not web_searched:
                                        await stream_callback(
                                            {"type": "status", "content": "Writing expanded response…"}
                                        )
                                    first_token_emitted = True
                                await stream_callback({"type": "token", "content": tok})

    except Exception as exc:
        fallback_triggered = True
        fallback_reason = str(exc)
        logger.warning(
            "expansion_search_error",
            error=str(exc),
            web_search_enabled=web_search_enabled,
            retrying_without_tools=web_search_enabled,
        )
        if web_search_enabled:
            # Retry without web search (e.g. API key does not have tool access)
            accumulated = ""
            extractor = _DirectAnswerExtractor()
            first_token_emitted = False
            web_searched = False
            try:
                async with _client().messages.stream(
                    model=settings.claude_model,
                    max_tokens=1500,
                    system=EXPANSION_SYNTHESIZE_SYSTEM,
                    messages=[{"role": "user", "content": user_content}],
                ) as stream2:
                    async for chunk in stream2.text_stream:
                        accumulated += chunk
                        tokens = extractor.feed(chunk)
                        for tok in tokens:
                            if tok and stream_callback:
                                if not first_token_emitted:
                                    await stream_callback(
                                        {"type": "status", "content": "Writing expanded response…"}
                                    )
                                    first_token_emitted = True
                                await stream_callback({"type": "token", "content": tok})
            except Exception as exc2:
                logger.error("expansion_fallback_failed", error=str(exc2))

    # Detect response language (CJK heuristic)
    cjk_count = sum(1 for ch in accumulated if "\u4e00" <= ch <= "\u9fff")
    response_language = "zh" if cjk_count > 5 else "en"

    # Extract JSON (model may prepend preamble text when using search)
    raw = _clean_json(_extract_json_object(accumulated))
    try:
        answer_json = json.loads(raw)
    except Exception:
        logger.warning("expansion_synthesize_parse_failed", raw=raw[:300])
        answer_json = _expansion_fallback_json(extractor._emitted, web_searched, raw_text=accumulated)

    answer_json["answer_mode"] = "external_expansion"
    answer_json["scope_label"] = "Beyond this paper"  # always hardcoded
    answer_json.setdefault("can_expand", False)

    logger.info(
        "expansion_search_complete",
        session_id=state.session_id,
        router_mode="external_expansion",
        web_search_enabled=web_search_enabled,
        tool_declared_in_request=web_search_enabled,
        tool_called_by_model=web_searched,
        search_queries_made=search_queries_made,
        used_external_results_in_synthesis=web_searched,
        scope_label_rendered=answer_json["scope_label"],
        response_language=response_language,
    )

    if stream_callback:
        await stream_callback({"type": "answer_json", "content": answer_json})

    latency_tool_ms: int | None = None
    if t_tool_start is not None and t_tool_end is not None:
        latency_tool_ms = round((t_tool_end - t_tool_start) * 1000)

    return {
        "answer_text": answer_json.get("direct_answer", ""),
        "answer_json": answer_json,
        "citations": [],
        "trace_metadata": {
            "tool_requested": web_search_enabled,
            "tool_called": web_searched,
            "tool_name": "web_search" if web_searched else None,
            "tool_success": web_searched,
            "search_results_count": search_queries_made,
            "fallback_triggered": fallback_triggered,
            "fallback_reason": fallback_reason if fallback_triggered else None,
            "latency_tool_ms": latency_tool_ms,
        },
    }


# ── Node 5d: SynthesizeNavigation ────────────────────────────────────────

async def synthesize_navigation(state: AgentState, stream_callback=None) -> dict:
    """Generate a lightweight navigation/next-steps response."""
    if stream_callback:
        await stream_callback({"type": "status", "content": "Mapping next steps…"})

    user_content = _language_note(state.session_language) + NAVIGATION_SYNTHESIZE_USER_TEMPLATE.format(
        title=state.paper_title or "this paper",
        session_summary=_build_session_context(state),
        question=state.question,
    )

    accumulated = ""
    extractor = _DirectAnswerExtractor()
    first_token_emitted = False

    logger.info("generation_start", session_id=state.session_id, mode="navigation_or_next_step")

    async with _client().messages.stream(
        model=settings.claude_model,
        max_tokens=800,
        system=NAVIGATION_SYNTHESIZE_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        async for chunk in stream.text_stream:
            accumulated += chunk
            tokens = extractor.feed(chunk)
            for tok in tokens:
                if tok and stream_callback:
                    if not first_token_emitted:
                        await stream_callback({"type": "status", "content": "Writing guidance…"})
                        first_token_emitted = True
                    await stream_callback({"type": "token", "content": tok})

    logger.info("generation_end", session_id=state.session_id, mode="navigation_or_next_step")

    raw = _clean_json(accumulated)
    try:
        answer_json = json.loads(raw)
    except Exception:
        logger.warning("navigation_synthesize_parse_failed", raw=raw[:300])
        answer_json = {
            "direct_answer": _sanitize_text(raw, "Unable to generate navigation guidance."),
            "key_points": None,
            "paper_context": None,
            "plain_language": None,
            "bigger_picture": None,
            "uncertainty": None,
            "answer_mode": "navigation_or_next_step",
            "scope_label": "Your learning path",
            "can_expand": False,
        }

    answer_json["answer_mode"] = "navigation_or_next_step"
    answer_json["scope_label"] = "Your learning path"
    answer_json.setdefault("can_expand", False)

    if stream_callback:
        await stream_callback({"type": "answer_json", "content": answer_json})

    return {
        "answer_text": answer_json.get("direct_answer", ""),
        "answer_json": answer_json,
        "citations": [],
    }


# ── Node 6: ExplainTerm ───────────────────────────────────────────────────

async def explain_term(state: AgentState, stream_callback=None) -> dict:
    """Explain unexplained technical terms that appeared in the answer."""
    terms = _extract_unknown_terms(state.answer_text, state.explained_terms)
    if not terms:
        return {}

    term = terms[0]
    message = await _client().messages.create(
        model=settings.claude_model,
        max_tokens=200,
        system=EXPLAIN_TERM_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Paper: {state.paper_title}\nTerm: {term}\nContext: {state.answer_text[:400]}",
        }],
    )
    explanation = message.content[0].text

    updated_json = state.answer_json.copy() if state.answer_json else {}
    existing_pl = updated_json.get("plain_language") or ""
    updated_json["plain_language"] = (existing_pl + f"\n\n**{term}**: {explanation}").strip()

    return {
        "unknown_terms": terms,
        "explained_terms": state.explained_terms + [term],
        "answer_json": updated_json,
    }


def _extract_unknown_terms(answer: str, already_explained: list[str]) -> list[str]:
    candidates = re.findall(r"\b([A-Z][a-zA-Z]{3,}(?:[A-Z][a-z]+)+)\b", answer)
    acronyms = re.findall(r"\b([A-Z]{2,6})\b", answer)
    common = {"This", "The", "In", "For", "With", "Our", "We", "ONLY", "Author", "Plain"}
    all_terms = list(dict.fromkeys(candidates + acronyms))
    return [t for t in all_terms if t not in common and t.lower() not in already_explained][:2]


# ── Node 7: UpdateSession ─────────────────────────────────────────────────

async def update_session(state: AgentState) -> dict:
    from app.db.redis_client import set_session_state

    new_covered = list(state.covered_question_ids)
    if state.question_id and state.question_id not in new_covered:
        new_covered.append(state.question_id)

    new_stages = list(state.covered_stages)
    if state.question_id:
        gq = next((q for q in state.guide_questions if q["id"] == state.question_id), None)
        if gq and gq.get("stage") not in new_stages:
            new_stages.append(gq["stage"])

    turn_count = state.turn_count + 1
    if turn_count % 5 == 0:
        new_summary = await _compress_summary(
            state.session_summary, state.question, state.answer_text
        )
    else:
        snippet = state.answer_text[:200].replace("\n", " ")
        new_summary = (
            state.session_summary + f"\nQ: {state.question}\nA: {snippet}"
        ).strip()[-2000:]

    # Language locking: detect once, never overwrite once set
    new_language = state.session_language or _detect_language(state.question)

    # Rolling recent messages (last 5 raw turns)
    answer_snippet = state.answer_text[:300].replace("\n", " ")
    new_recent = (state.recent_messages + [{
        "q": state.question,
        "a": answer_snippet,
        "mode": state.answer_mode,
    }])[-5:]

    session_state = {
        "paper_id": state.paper_id,
        "covered_question_ids": new_covered,
        "covered_stages": new_stages,
        "explained_terms": state.explained_terms,
        "session_summary": new_summary,
        "turn_count": turn_count,
        "suggested_questions": state.suggested_questions,
        "recent_messages": new_recent,
        "session_language": new_language,
    }
    await set_session_state(state.session_id, session_state)

    logger.info(
        "session_updated",
        session_id=state.session_id,
        turn_count=turn_count,
        session_language=new_language,
        recent_messages_count=len(new_recent),
        covered_questions=len(new_covered),
    )

    return {
        "covered_question_ids": new_covered,
        "covered_stages": new_stages,
        "session_summary": new_summary,
        "session_language": new_language,
        "recent_messages": new_recent,
        "turn_count": turn_count,
    }


def _detect_language(text: str) -> str:
    """Return 'zh' if text is predominantly CJK, 'en' otherwise."""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return "zh" if cjk > 2 else "en"


def _build_session_context(state: AgentState) -> str:
    """
    Build a rich session context block for synthesis prompts.
    Combines the running summary with the last ≤3 raw turns for granularity.
    """
    parts: list[str] = []
    if state.session_summary:
        parts.append(f"Summary: {state.session_summary}")
    if state.recent_messages:
        turns = "\n".join(
            f"Q: {m['q']}\nA: {m.get('a', '')[:200]}"
            for m in state.recent_messages[-3:]
        )
        parts.append(f"Recent turns:\n{turns}")
    return "\n\n".join(parts) if parts else "First turn."


def _language_note(session_language: str) -> str:
    """Return a language-locking prefix line for synthesis prompts."""
    if not session_language:
        return ""
    lang_name = {"zh": "Chinese", "en": "English"}.get(session_language, session_language)
    return f"[Session language: {lang_name}. Write ALL response fields in {lang_name}.]\n\n"


async def _compress_summary(current: str, last_q: str, last_a: str) -> str:
    msg = await _client().messages.create(
        model=settings.claude_model,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": SESSION_COMPRESS_PROMPT.format(
                current_summary=current,
                last_q=last_q,
                last_a=last_a[:300],
            ),
        }],
    )
    return msg.content[0].text.strip()


# ── Node 8: SuggestNext ───────────────────────────────────────────────────

STAGE_ORDER = ["motivation", "approach", "experiments", "takeaways"]


def _pick_primary(remaining: list[dict], current_stage: str) -> dict | None:
    current_stage_remaining = [q for q in remaining if q["stage"] == current_stage]
    if current_stage_remaining:
        return current_stage_remaining[0]
    current_idx = STAGE_ORDER.index(current_stage) if current_stage in STAGE_ORDER else 0
    for stage in STAGE_ORDER[current_idx + 1:]:
        next_stage_qs = [q for q in remaining if q["stage"] == stage]
        if next_stage_qs:
            return next_stage_qs[0]
    return remaining[0] if remaining else None


async def suggest_next(state: AgentState) -> dict:
    """Return up to 3 suggested questions: 1 primary + 2 secondary."""
    covered = set(state.covered_question_ids)
    remaining = [q for q in state.guide_questions if q["id"] not in covered]
    if not remaining:
        return {"next_question": None, "suggested_questions": []}

    current_stage = "motivation"
    if state.question_id:
        gq = next((q for q in state.guide_questions if q["id"] == state.question_id), None)
        if gq:
            current_stage = gq["stage"]

    primary = _pick_primary(remaining, current_stage)
    suggestions: list[dict] = []

    if primary:
        suggestions.append({
            "id": primary["id"],
            "question": primary["question"],
            "stage": primary["stage"],
            "is_primary": True,
        })

    secondary_pool = [q for q in remaining if q["id"] != (primary["id"] if primary else None)]
    primary_stage = primary["stage"] if primary else current_stage
    other_stages = [q for q in secondary_pool if q["stage"] != primary_stage]
    same_stage = [q for q in secondary_pool if q["stage"] == primary_stage]
    secondary_candidates = other_stages + same_stage

    for q in secondary_candidates[:2]:
        suggestions.append({
            "id": q["id"],
            "question": q["question"],
            "stage": q["stage"],
            "is_primary": False,
        })

    primary_item = suggestions[0] if suggestions else None
    return {
        "next_question": {
            "id": primary_item["id"],
            "question": primary_item["question"],
            "stage": primary_item["stage"],
        } if primary_item else None,
        "suggested_questions": suggestions,
    }
