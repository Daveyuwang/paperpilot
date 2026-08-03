from __future__ import annotations

from typing import TypedDict

from app.deep_research.models import (
    BudgetSnapshot,
    PostSynthesisRoutingDecision,
    PostSynthesisEvaluationRun,
    PreSynthesisEvaluationRun,
    RepairStage,
    ResearchReport,
    RoutingDecision,
    SubQuestion,
    SubReport,
)


class FailedQuery(TypedDict):
    sub_question_id: str
    query: list[str]
    error_code: str
    reason: str


def merge_sub_reports(
    existing: list[SubReport] | None,
    updates: list[SubReport] | None,
) -> list[SubReport]:
    """Build a report snapshot without accumulating duplicate reports.

    Reports are stable state entities keyed by ``sub_question_id``. A non-empty
    execution batch replaces an existing report in place or appends a new one.
    An empty update is an explicit full reset; callers with no report update
    should omit ``sub_reports`` from their returned state instead.
    """
    if updates is None:
        return list(existing or [])

    incoming = list(updates)
    if not incoming:
        return []

    merged: list[SubReport] = []
    positions: dict[str, int] = {}

    for report in existing or []:
        report_id = report.sub_question_id
        if report_id in positions:
            merged[positions[report_id]] = report
        else:
            positions[report_id] = len(merged)
            merged.append(report)

    for report in incoming:
        report_id = report.sub_question_id
        if report_id in positions:
            merged[positions[report_id]] = report
        else:
            positions[report_id] = len(merged)
            merged.append(report)

    return merged


class DeepResearchState(TypedDict, total=False):
    topic: str
    user_sources: list[str]
    depth: str  # "quick" | "standard" | "deep"
    sub_questions: list[SubQuestion]
    sub_reports: list[SubReport]
    failed_queries: list[FailedQuery]
    execution_target_ids: list[str] | None
    pre_synthesis_evaluation_run: PreSynthesisEvaluationRun | None
    candidate_report: ResearchReport | None
    post_synthesis_evaluation_run: PostSynthesisEvaluationRun | None
    post_evaluation_history: list[PostSynthesisEvaluationRun]
    post_synthesis_controller_decision: PostSynthesisRoutingDecision | None
    post_routing_history: list[PostSynthesisRoutingDecision]
    post_recovery_fingerprints: list[str]
    target_report_segment_ids: list[str]
    report_revision_count: int
    report_revision_status: str | None
    report_version: int
    report_accepted: bool
    controller_decision: RoutingDecision | None
    routing_history: list[RoutingDecision]
    repair_stage: RepairStage
    budget_snapshot: BudgetSnapshot
    recovery_fingerprints: list[str]
    plan_version: int
    corpus_version: int
    repair_preparation_status: str | None
    execute_status: str | None
    workflow_error_code: str | None
    terminal_status: str | None
    terminal_reason: str | None
    final_report: ResearchReport | None
