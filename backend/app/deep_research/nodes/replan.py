from __future__ import annotations

import structlog

from app.deep_research.config import CONFIDENCE_THRESHOLD, MAX_REPLAN
from app.deep_research.llm_factory import make_llm
from app.deep_research.models import Plan
from app.deep_research.prompts import REPLAN_SYSTEM, REPLAN_USER
from app.deep_research.state import DeepResearchState

logger = structlog.get_logger()


def should_continue(state: DeepResearchState) -> str:
    sub_reports = state.get("sub_reports", [])
    replan_count = state.get("replan_count", 0)

    if not sub_reports or replan_count >= MAX_REPLAN:
        return "synthesize"

    low_confidence = [r for r in sub_reports if r.confidence < CONFIDENCE_THRESHOLD]
    failed = state.get("failed_queries", [])

    if len(low_confidence) > len(sub_reports) / 2 or len(failed) > len(sub_reports) / 2:
        logger.info(
            "replan_triggered",
            low_confidence=len(low_confidence),
            failed=len(failed),
            total=len(sub_reports),
            replan_count=replan_count,
        )
        return "replan"

    return "synthesize"


async def replan_node(state: DeepResearchState) -> dict:
    topic = state["topic"]
    sub_reports = state.get("sub_reports", [])
    failed_queries = state.get("failed_queries", [])
    replan_count = state.get("replan_count", 0)

    existing_ids = [r.sub_question_id for r in sub_reports]
    next_id = len(existing_ids) + 1

    low_reports = [r for r in sub_reports if r.confidence < CONFIDENCE_THRESHOLD]
    low_reports_text = "\n\n".join(
        f"Question: {r.question}\nConfidence: {r.confidence}\nGaps: {r.gaps}"
        for r in low_reports
    )
    failed_text = "\n".join(
        f"- Queries: {f.get('query', '?')}, Reason: {f.get('reason', 'unknown')}"
        for f in failed_queries
    ) if failed_queries else "(none)"

    system = REPLAN_SYSTEM.format(next_id=next_id)
    user_msg = REPLAN_USER.format(
        topic=topic,
        low_confidence_reports=low_reports_text or "(none)",
        failed_queries=failed_text,
    )

    llm = make_llm(state, max_tokens=1500, temperature=0.3)
    structured_llm = llm.with_structured_output(Plan)

    try:
        plan: Plan = await structured_llm.ainvoke(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ]
        )
        logger.info(
            "replan_node_completed",
            new_questions=len(plan.sub_questions),
            replan_count=replan_count + 1,
        )
        return {
            "sub_questions": plan.sub_questions,
            "replan_count": replan_count + 1,
        }
    except Exception as exc:
        logger.error("replan_node_failed", error=str(exc))
        return {"replan_count": replan_count + 1}
