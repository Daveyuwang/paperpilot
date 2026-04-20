"""
Generate node — produces structured answer from filtered evidence chunks.
"""
from __future__ import annotations

import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from app.agents.agentic_rag.config import get_model_name, get_anthropic_api_key
from app.agents.agentic_rag.prompts import GENERATE_SYSTEM, GENERATE_USER_TEMPLATE


async def generate_node(state: dict) -> dict:
    """Generate a structured answer from filtered evidence chunks."""
    filtered_chunks = state.get("filtered_chunks", [])
    question = ""
    for msg in state["messages"]:
        if hasattr(msg, "type") and msg.type == "human":
            question = msg.content
            break

    # Build evidence block
    evidence_lines = []
    for i, chunk in enumerate(filtered_chunks, 1):
        section = chunk.get("section_title") or "?"
        page = chunk.get("page_number") or "?"
        content = (chunk.get("content") or "")[:600]
        score = chunk.get("score", 0)
        evidence_lines.append(
            f"[Chunk {i}] §{section}, p.{page} (score: {score:.3f}):\n{content}"
        )

    evidence_block = "\n\n".join(evidence_lines) if evidence_lines else "No relevant evidence retrieved."
    external_block = ""

    user_content = GENERATE_USER_TEMPLATE.format(
        paper_title=state.get("paper_title", ""),
        session_context=state.get("session_summary", "First turn."),
        question=question,
        evidence_block=evidence_block,
        external_block=external_block,
    )

    llm = ChatAnthropic(
        model=get_model_name(),
        api_key=get_anthropic_api_key(),
        max_tokens=1400,
        temperature=0.2,
    )

    response = await llm.ainvoke([
        SystemMessage(content=GENERATE_SYSTEM),
        HumanMessage(content=user_content),
    ])

    raw = response.content.strip()
    # Strip markdown fences
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        answer = json.loads(raw)
    except json.JSONDecodeError:
        answer = {
            "direct_answer": raw[:600],
            "key_points": None,
            "evidence": [],
            "plain_language": None,
            "bigger_picture": None,
            "uncertainty": "Answer format could not be parsed.",
            "answer_mode": "paper_understanding",
            "scope_label": "Using this paper",
            "can_expand": True,
        }

    answer.setdefault("answer_mode", "paper_understanding")
    answer.setdefault("scope_label", "Using this paper")
    answer.setdefault("can_expand", True)

    # Build citations from filtered chunks
    citations = []
    for chunk in filtered_chunks:
        if chunk.get("chunk_id"):
            citations.append({
                "chunk_id": chunk["chunk_id"],
                "section_title": chunk.get("section_title"),
                "page_number": chunk.get("page_number"),
                "bbox": chunk.get("bbox"),
            })

    answer["_citations"] = citations
    return {"answer": answer}
