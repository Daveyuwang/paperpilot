from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from app.deep_research.models import SubQuestion, SubReport, ResearchReport


class DeepResearchState(TypedDict, total=False):
    topic: str
    user_sources: list[str]
    depth: str  # "quick" | "standard" | "deep"
    workspace_id: str
    api_key: str
    llm_base_url: str | None
    llm_model: str | None
    sub_questions: list[SubQuestion]
    sub_reports: Annotated[list[SubReport], operator.add]
    failed_queries: list[dict]
    replan_count: int
    final_report: ResearchReport | None
