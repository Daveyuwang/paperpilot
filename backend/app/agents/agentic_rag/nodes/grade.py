"""
Grade node — checks if the generated answer is grounded and addresses the question.
On failure, provides a rewritten query for retry.
"""
from __future__ import annotations

import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from app.agents.agentic_rag.config import get_model_name, get_anthropic_api_key
from app.agents.agentic_rag.prompts import GRADE_SYSTEM, GRADE_USER_TEMPLATE


async def grade_node(state: dict) -> dict:
    """Grade the generated answer for grounding and relevance."""
    answer = state.get("answer")
    if not answer:
        return {"grade_result": "fail", "rewritten_query": "", "retry_count": state.get("retry_count", 0)}

    question = ""
    for msg in state["messages"]:
        if hasattr(msg, "type") and msg.type == "human":
            question = msg.content
            break

    # Build evidence summary for grading
    filtered_chunks = state.get("filtered_chunks", [])
    evidence_lines = []
    for chunk in filtered_chunks[:5]:
        content = (chunk.get("content") or "")[:300]
        evidence_lines.append(content)
    evidence_text = "\n---\n".join(evidence_lines) if evidence_lines else "No evidence."

    answer_text = answer.get("direct_answer", "") + "\n" + "\n".join(answer.get("key_points") or [])

    user_content = GRADE_USER_TEMPLATE.format(
        question=question,
        answer=answer_text,
        evidence=evidence_text,
    )

    llm = ChatAnthropic(
        model=get_model_name(),
        api_key=get_anthropic_api_key(),
        max_tokens=300,
        temperature=0.1,
    )

    response = await llm.ainvoke([
        SystemMessage(content=GRADE_SYSTEM),
        HumanMessage(content=user_content),
    ])

    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        grade = json.loads(raw)
    except json.JSONDecodeError:
        # If we can't parse, assume pass to avoid infinite loops
        return {"grade_result": "pass", "rewritten_query": ""}

    passed = grade.get("pass", True)
    rewritten = grade.get("rewritten_query", "")
    retry_count = state.get("retry_count", 0)

    if not passed:
        retry_count += 1

    return {
        "grade_result": "pass" if passed else "fail",
        "rewritten_query": rewritten,
        "retry_count": retry_count,
    }
