from __future__ import annotations

import time
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.deep_research.config import DEPTH_CONFIG
from app.deep_research.llm_factory import make_llm
from app.deep_research.models import Plan
from app.deep_research.prompts import PLAN_SYSTEM, PLAN_USER
from app.deep_research.state import DeepResearchState

logger = structlog.get_logger()


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def _invoke_plan(structured_llm, messages):
    return await structured_llm.ainvoke(messages)


async def plan_node(state: DeepResearchState) -> dict:
    topic = state["topic"]
    depth = state.get("depth", "standard")
    user_sources = state.get("user_sources", [])
    t0 = time.monotonic()

    max_questions = DEPTH_CONFIG.get(depth, 5)
    min_questions = max(3, max_questions - 2)

    sources_block = ""
    if user_sources:
        sources_block = "User-provided sources:\n" + "\n".join(f"- {s}" for s in user_sources)

    system = PLAN_SYSTEM.format(min_questions=min_questions, max_questions=max_questions)
    user_msg = PLAN_USER.format(
        topic=topic,
        depth=depth,
        max_questions=max_questions,
        sources_block=sources_block,
    )

    llm = make_llm(state, max_tokens=2000, temperature=0.3)
    structured_llm = llm.with_structured_output(Plan)

    try:
        plan: Plan = await _invoke_plan(
            structured_llm,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ]
        )
        logger.info(
            "plan_node_completed",
            topic=topic,
            num_sub_questions=len(plan.sub_questions),
            elapsed_s=round(time.monotonic() - t0, 2),
        )
        return {"sub_questions": plan.sub_questions}
    except Exception as exc:
        logger.error("plan_node_failed", topic=topic, error=str(exc))
        raise
