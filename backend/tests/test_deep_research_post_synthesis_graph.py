"""Graph contract tests for the bounded post-synthesis repair loop."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.deep_research import graph as graph_module
from app.deep_research.models import (
    AtomicClaimAudit,
    BudgetSnapshot,
    ClaimCitationAudit,
    ClaimEvidenceReference,
    PostSynthesisEvaluation,
    PostSynthesisEvaluationRun,
    PostSynthesisScores,
    ReportEvaluationIssue,
    ReportRevisionPatch,
    ReportSection,
    ReportSegmentRevision,
    ReportSegmentAudit,
    ResearchReport,
    SourceRef,
    SubQuestion,
    SubReport,
)
from app.deep_research.provenance import (
    build_evidence_inventory,
    build_report_segments,
    report_digest,
)
from app.deep_research.nodes import revise_report as revise_report_module
from app.deep_research.nodes import synthesize as synthesize_module


def _question(question_id: str) -> SubQuestion:
    return SubQuestion(
        id=question_id,
        question=f"Question {question_id}",
        search_queries=[f"query {question_id}"],
        priority=1,
        rationale="Required by the graph fixture.",
    )


def _source(question_id: str) -> SourceRef:
    return SourceRef(
        url=f"https://evidence.example/{question_id}",
        title=f"Evidence {question_id}",
        excerpt=f"Direct source excerpt for {question_id} versioned evidence.",
        published_at="2026-07-01",
        source_type="primary_document",
    )


def _sub_report(question_id: str, *, version: int = 1) -> SubReport:
    return SubReport(
        sub_question_id=question_id,
        question=f"Question {question_id}",
        findings=f"Finding {question_id} version {version}",
        key_facts=[f"Fact {question_id} version {version}"],
        confidence=0.9,
        gaps="",
        sources=[_source(question_id)],
    )


def _report(version: int) -> ResearchReport:
    sources, evidence = build_evidence_inventory(
        [_sub_report("sq-a"), _sub_report("sq-b")]
    )
    unit = next(
        item for item in evidence if item.provenance == "source_excerpt"
    )
    marker = f"[E:{unit.evidence_id}] [S:{unit.source_ids[0]}]"
    return ResearchReport(
        title=f"Candidate report version {version}",
        executive_summary=f"Executive summary version {version}. {marker}",
        sections=[
            ReportSection(
                heading="Evidence",
                content=f"Evidence-backed section version {version}. {marker}",
            )
        ],
        key_findings=[f"Grounded finding version {version}. {marker}"],
        limitations="Bounded limitations.",
        sources=[
            SourceRef(
                source_id=source.source_id,
                url=source.url,
                title=source.title,
                published_at=source.published_at,
                source_type=source.source_type,
            )
            for source in sources
        ],
    )


def _initial_state() -> dict[str, Any]:
    return {
        "topic": "Post-synthesis graph test",
        "user_sources": [],
        "depth": "standard",
        "sub_questions": [],
        "sub_reports": [],
        "failed_queries": [],
        "candidate_report": None,
        "final_report": None,
        "report_accepted": False,
        "post_evaluation_history": [],
        "post_synthesis_controller_decision": None,
        "post_routing_history": [],
        "post_recovery_fingerprints": [],
        "target_report_segment_ids": [],
        "report_revision_count": 0,
        "report_version": 0,
        "plan_version": 1,
        "budget_snapshot": BudgetSnapshot(),
        "routing_history": [],
        "recovery_fingerprints": [],
    }


def _patch_common_graph(
    monkeypatch: pytest.MonkeyPatch,
    *,
    post_routes: list[str],
    trace: list[str],
    execute_targets: list[list[str] | None] | None = None,
    on_candidate_review: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Patch module-local callables, then callers compile a fresh graph."""
    questions = [_question("sq-a"), _question("sq-b")]
    reports = [_sub_report("sq-a"), _sub_report("sq-b")]
    post_route_iter = iter(post_routes)
    current_post_route = "stop_incomplete"
    synthesis_round = 0

    async def plan(_state):
        trace.append("plan")
        return {"sub_questions": questions, "plan_version": 1}

    async def execute(state):
        trace.append("execute")
        if execute_targets is not None:
            execute_targets.append(state.get("execution_target_ids"))
        version = 2 if state.get("execution_target_ids") else 1
        return {
            "sub_reports": [
                _sub_report("sq-a", version=version),
                _sub_report("sq-b"),
            ],
            "failed_queries": [],
            "execution_target_ids": None,
            "execute_status": "completed",
        }

    async def pre_evaluate(_state):
        trace.append("pre_evaluate")
        return {"workflow_error_code": None}

    def pre_controller(_state):
        trace.append("pre_controller")
        return {"terminal_status": None}

    def pre_route(_state):
        return "accept"

    async def synthesize(_state):
        nonlocal synthesis_round
        synthesis_round += 1
        trace.append("synthesize")
        return {
            "candidate_report": _report(synthesis_round),
            "final_report": None,
            "report_accepted": False,
            "report_version": synthesis_round,
        }

    async def post_evaluate(state):
        trace.append("post_evaluate")
        if on_candidate_review is not None:
            on_candidate_review(state)
        return {"report_accepted": False}

    def post_controller(_state):
        nonlocal current_post_route
        trace.append("post_controller")
        current_post_route = next(post_route_iter)
        update: dict[str, Any] = {"report_accepted": False}
        if current_post_route == "targeted_evidence":
            update["execution_target_ids"] = ["sq-a"]
        elif current_post_route == "targeted_synthesis":
            update["target_report_segment_ids"] = ["seg-executive-summary"]
        return update

    def post_route(_state):
        return current_post_route

    async def revise(state):
        trace.append("revise_report")
        assert state.get("execution_target_ids") is None
        assert state.get("target_report_segment_ids") == [
            "seg-executive-summary"
        ]
        return {
            "candidate_report": _report(2),
            "report_revision_count": state.get("report_revision_count", 0) + 1,
            "report_revision_status": "completed",
            "target_report_segment_ids": [],
            "final_report": None,
            "report_accepted": False,
            "report_version": 2,
        }

    def revision_route(state):
        return (
            "evaluate_report"
            if state.get("report_revision_status") == "completed"
            else "stop_incomplete"
        )

    async def targeted_repair(state):
        trace.append("targeted_repair")
        assert state.get("execution_target_ids") == ["sq-a"]
        return {"repair_preparation_status": "ready"}

    async def partial_replan(_state):
        trace.append("partial_replan")
        return {
            "execution_target_ids": ["sq-a"],
            "repair_preparation_status": "ready",
            "plan_version": 2,
        }

    async def full_replan(_state):
        trace.append("full_replan")
        return {
            "execution_target_ids": ["sq-a", "sq-b"],
            "repair_preparation_status": "ready",
            "plan_version": 2,
            "sub_reports": [],
            "candidate_report": None,
            "final_report": None,
            "report_accepted": False,
        }

    def repair_route(state):
        return (
            "execute"
            if state.get("repair_preparation_status") == "ready"
            else "stop_incomplete"
        )

    def finalize_complete(state):
        trace.append("finalize_complete")
        return {
            "final_report": state["candidate_report"],
            "report_accepted": True,
            "terminal_status": "completed",
            "terminal_reason": None,
        }

    def finalize_incomplete(_state):
        trace.append("finalize_incomplete")
        return {
            "final_report": None,
            "report_accepted": False,
            "terminal_status": "incomplete",
            "terminal_reason": "Post-synthesis quality gate did not pass.",
        }

    monkeypatch.setattr(graph_module, "plan_node", plan)
    monkeypatch.setattr(graph_module, "execute_node", execute)
    monkeypatch.setattr(graph_module, "route_after_execute", lambda _state: "evaluate")
    monkeypatch.setattr(graph_module, "evidence_evaluate_node", pre_evaluate)
    monkeypatch.setattr(graph_module, "controller_node", pre_controller)
    monkeypatch.setattr(graph_module, "route_after_controller", pre_route)
    monkeypatch.setattr(graph_module, "synthesize_node", synthesize)
    monkeypatch.setattr(graph_module, "post_synthesis_evaluate_node", post_evaluate)
    monkeypatch.setattr(graph_module, "post_synthesis_controller_node", post_controller)
    monkeypatch.setattr(graph_module, "route_after_post_controller", post_route)
    monkeypatch.setattr(graph_module, "revise_report_node", revise)
    monkeypatch.setattr(graph_module, "route_after_report_revision", revision_route)
    monkeypatch.setattr(graph_module, "prepare_targeted_repair_node", targeted_repair)
    monkeypatch.setattr(graph_module, "partial_replan_node", partial_replan)
    monkeypatch.setattr(graph_module, "full_replan_node", full_replan)
    monkeypatch.setattr(graph_module, "route_after_repair_preparation", repair_route)
    monkeypatch.setattr(graph_module, "finalize_complete_node", finalize_complete)
    monkeypatch.setattr(graph_module, "finalize_incomplete_node", finalize_incomplete)


@pytest.mark.asyncio
async def test_direct_accept_publishes_only_after_post_quality_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    reviewed: list[tuple[Any, Any]] = []

    def inspect_rejected_draft(state: dict[str, Any]) -> None:
        reviewed.append((state.get("final_report"), state.get("report_accepted")))

    _patch_common_graph(
        monkeypatch,
        post_routes=["accept"],
        trace=trace,
        on_candidate_review=inspect_rejected_draft,
    )

    result = await graph_module.build_graph().compile().ainvoke(_initial_state())

    assert reviewed == [(None, False)]
    assert trace == [
        "plan",
        "execute",
        "pre_evaluate",
        "pre_controller",
        "synthesize",
        "post_evaluate",
        "post_controller",
        "finalize_complete",
    ]
    assert result["report_accepted"] is True
    assert result["terminal_status"] == "completed"
    assert result["final_report"].title == "Candidate report version 1"


@pytest.mark.asyncio
async def test_overstatement_revision_reevaluates_without_search_then_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    execute_targets: list[list[str] | None] = []
    _patch_common_graph(
        monkeypatch,
        post_routes=["targeted_synthesis", "accept"],
        trace=trace,
        execute_targets=execute_targets,
    )

    result = await graph_module.build_graph().compile().ainvoke(
        _initial_state(),
        config={"recursion_limit": 30},
    )

    assert execute_targets == [None]
    assert trace.count("execute") == 1
    assert trace.count("post_evaluate") == 2
    assert trace.count("revise_report") == 1
    assert trace.index("revise_report") < len(trace) - 1
    assert result["report_accepted"] is True
    assert result["report_revision_count"] == 1
    assert result["final_report"].title == "Candidate report version 2"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("post_route", "repair_node"),
    [
        ("targeted_evidence", "targeted_repair"),
        ("partial_replan", "partial_replan"),
        ("full_replan", "full_replan"),
    ],
)
async def test_evidence_or_plan_repair_returns_through_pre_eval_and_resynthesis(
    monkeypatch: pytest.MonkeyPatch,
    post_route: str,
    repair_node: str,
) -> None:
    trace: list[str] = []
    execute_targets: list[list[str] | None] = []
    _patch_common_graph(
        monkeypatch,
        post_routes=[post_route, "accept"],
        trace=trace,
        execute_targets=execute_targets,
    )

    result = await graph_module.build_graph().compile().ainvoke(
        _initial_state(),
        config={"recursion_limit": 40},
    )

    assert trace.count(repair_node) == 1
    assert trace.count("execute") == 2
    assert trace.count("pre_evaluate") == 2
    assert trace.count("synthesize") == 2
    assert trace.count("post_evaluate") == 2
    assert execute_targets[0] is None
    if post_route in {"targeted_evidence", "partial_replan"}:
        assert execute_targets[1] == ["sq-a"]
    else:
        assert execute_targets[1] == ["sq-a", "sq-b"]
    assert result["report_accepted"] is True
    assert result["final_report"] is not None


@pytest.mark.asyncio
async def test_post_controller_stop_never_publishes_rejected_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    _patch_common_graph(
        monkeypatch,
        post_routes=["stop_incomplete"],
        trace=trace,
    )

    result = await graph_module.build_graph().compile().ainvoke(_initial_state())

    assert result["terminal_status"] == "incomplete"
    assert result["report_accepted"] is False
    assert result["final_report"] is None
    assert result["candidate_report"] is not None
    assert "finalize_complete" not in trace
    assert trace[-1] == "finalize_incomplete"


def _writing_defect_run(state: dict[str, Any]) -> PostSynthesisEvaluationRun:
    sources, evidence = build_evidence_inventory(state["sub_reports"])
    unit = next(
        item for item in evidence if item.provenance == "source_excerpt"
    )
    assert sources and unit.source_ids
    source_id = unit.source_ids[0]
    segments = build_report_segments(state["candidate_report"])
    audits: list[ReportSegmentAudit] = []
    executive_claim_id = ""
    executive_segment_id = ""
    for index, segment in enumerate(segments):
        is_material_surface = segment.component in {
            "executive_summary",
            "section",
            "key_finding",
        }
        if not is_material_surface:
            audits.append(
                ReportSegmentAudit(
                    segment_id=segment.id,
                    contains_material_claims=False,
                    claims=[],
                )
            )
            continue
        claim_id = f"claim-{index:03d}"
        is_executive = segment.component == "executive_summary"
        if is_executive:
            executive_claim_id = claim_id
            executive_segment_id = segment.id
        audits.append(
            ReportSegmentAudit(
                segment_id=segment.id,
                contains_material_claims=True,
                claims=[
                    AtomicClaimAudit(
                        claim_id=claim_id,
                        claim_text=segment.text,
                        materiality="major",
                        support="supported",
                        evidence_refs=[
                            ClaimEvidenceReference(
                                evidence_id=unit.evidence_id,
                                supporting_excerpt=unit.text,
                            )
                        ],
                        citation=ClaimCitationAudit(
                            status="correct",
                            cited_source_ids=[source_id],
                            rationale="The retained source is valid for this fixture.",
                        ),
                        calibration="overstated" if is_executive else "accurate",
                        rationale="Writing-only defect fixture.",
                    )
                ],
            )
        )
    issue = ReportEvaluationIssue(
        id="issue-repeated-overstatement",
        category="overstatement",
        severity="major",
        claim_ids=[executive_claim_id],
        segment_ids=[executive_segment_id],
        affected_sub_question_ids=[],
        suggested_repair_stage="synthesis",
        description="The same overstatement remains after revision.",
        acceptance_criteria=["Calibrate the executive summary wording."],
    )
    return PostSynthesisEvaluationRun(
        status="completed",
        evaluation=PostSynthesisEvaluation(
            schema_version="post-synthesis-eval.v1",
            rubric_version="report-quality.v1",
            segment_audits=audits,
            scores=PostSynthesisScores(
                intent_alignment=95,
                material_claim_grounding=95,
                citation_fidelity=95,
                citation_completeness=95,
                contradiction_handling=95,
                coverage=95,
                coherence=95,
                limitations_calibration=95,
            ),
            issues=[issue],
            unresolved_questions=[],
            summary="One writing-only defect remains.",
        ),
        error_code=None,
        report_digest=report_digest(state["candidate_report"]),
        report_version=state["report_version"],
        evaluator_model="test-post-evaluator",
        attempts=1,
        duration_ms=2,
    )


@pytest.mark.asyncio
async def test_real_post_controller_budget_has_exact_finite_revision_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    real_controller = graph_module.post_synthesis_controller_node
    real_post_route = graph_module.route_after_post_controller
    _patch_common_graph(
        monkeypatch,
        post_routes=[],
        trace=trace,
    )

    async def repeated_post_evaluation(state):
        trace.append("post_evaluate")
        run = _writing_defect_run(state)
        return {
            "post_synthesis_evaluation_run": run,
            "post_evaluation_history": [
                *state.get("post_evaluation_history", []),
                run,
            ],
            "report_accepted": False,
        }

    def traced_real_controller(state):
        trace.append("post_controller")
        return real_controller(state)

    monkeypatch.setattr(
        graph_module,
        "post_synthesis_evaluate_node",
        repeated_post_evaluation,
    )
    monkeypatch.setattr(
        graph_module,
        "post_synthesis_controller_node",
        traced_real_controller,
    )
    monkeypatch.setattr(
        graph_module,
        "route_after_post_controller",
        real_post_route,
    )
    initial = _initial_state()
    initial["budget_snapshot"] = BudgetSnapshot(
        post_evaluation_limit=2,
        synthesis_repair_limit=1,
    )

    result = await graph_module.build_graph().compile().ainvoke(
        initial,
        config={"recursion_limit": 30},
    )

    assert trace == [
        "plan",
        "execute",
        "pre_evaluate",
        "pre_controller",
        "synthesize",
        "post_evaluate",
        "post_controller",
        "revise_report",
        "post_evaluate",
        "post_controller",
        "finalize_incomplete",
    ]
    assert trace.count("execute") == 1
    assert trace.count("revise_report") == 1
    assert trace.count("post_evaluate") == 2
    assert result["terminal_status"] == "incomplete"
    assert result["report_accepted"] is False
    assert result["final_report"] is None


def test_failed_revision_route_is_fail_closed() -> None:
    assert graph_module.route_after_report_revision(
        {"report_revision_status": "failed"}
    ) == "stop_incomplete"
    assert graph_module.route_after_report_revision({}) == "stop_incomplete"


class _FakeRevisionLLM:
    def __init__(self, response: Any):
        self.response = response
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any) -> Any:
        self.calls.append(messages)
        return self.response


@pytest.mark.asyncio
async def test_revision_node_changes_only_authorized_segment_and_keeps_draft_unpublished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _initial_state()
    state.update(
        {
            "api_key": "REVISION-SECRET",
            "sub_questions": [_question("sq-a"), _question("sq-b")],
            "sub_reports": [_sub_report("sq-a"), _sub_report("sq-b")],
            "candidate_report": _report(1),
            "target_report_segment_ids": ["seg-executive-summary"],
            "report_version": 1,
        }
    )
    state["post_synthesis_evaluation_run"] = _writing_defect_run(state)
    _, evidence = build_evidence_inventory(state["sub_reports"])
    unit = evidence[0]
    assert unit.source_ids
    revised_text = (
        "Calibrated revised executive summary "
        f"[E:{unit.evidence_id}] [S:{unit.source_ids[0]}]."
    )
    fake = _FakeRevisionLLM(
        ReportRevisionPatch(
            schema_version="report-revision.v1",
            resolved_issue_ids=["issue-repeated-overstatement"],
            updates=[
                ReportSegmentRevision(
                    segment_id="seg-executive-summary",
                    revised_text=revised_text,
                )
            ],
        )
    )
    monkeypatch.setattr(
        revise_report_module,
        "make_structured_llm",
        lambda *_args, **_kwargs: fake,
    )
    before = state["candidate_report"].model_copy(deep=True)

    update = await revise_report_module.revise_report_node(state)

    revised = update["candidate_report"]
    assert update["report_revision_status"] == "completed"
    assert update["report_revision_count"] == 1
    assert update["report_version"] == 2
    assert update["target_report_segment_ids"] == []
    assert update["final_report"] is None
    assert update["report_accepted"] is False
    assert revised.executive_summary == revised_text
    assert revised.title == before.title
    assert revised.sections == before.sections
    assert revised.key_findings == before.key_findings
    assert revised.limitations == before.limitations
    assert revised.sources == before.sources
    assert revise_report_module.route_after_report_revision(update) == "evaluate_report"
    prompt = str(fake.calls)
    assert "REVISION-SECRET" not in prompt
    assert "seg-executive-summary" in prompt
    assert unit.evidence_id in prompt


@pytest.mark.asyncio
async def test_revision_patch_outside_authorized_scope_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _initial_state()
    state.update(
        {
            "sub_questions": [_question("sq-a"), _question("sq-b")],
            "sub_reports": [_sub_report("sq-a"), _sub_report("sq-b")],
            "candidate_report": _report(1),
            "target_report_segment_ids": ["seg-executive-summary"],
            "report_version": 1,
        }
    )
    state["post_synthesis_evaluation_run"] = _writing_defect_run(state)
    _, evidence = build_evidence_inventory(state["sub_reports"])
    unit = evidence[0]
    fake = _FakeRevisionLLM(
        ReportRevisionPatch(
            schema_version="report-revision.v1",
            resolved_issue_ids=["issue-repeated-overstatement"],
            updates=[
                ReportSegmentRevision(
                    segment_id="seg-section-000",
                    revised_text=(
                        "Unauthorized change "
                        f"[E:{unit.evidence_id}] [S:{unit.source_ids[0]}]."
                    ),
                )
            ],
        )
    )
    monkeypatch.setattr(
        revise_report_module,
        "make_structured_llm",
        lambda *_args, **_kwargs: fake,
    )

    update = await revise_report_module.revise_report_node(state)

    assert update["report_revision_status"] == "failed"
    assert update["workflow_error_code"] == "report_revision_invalid_references"
    assert update["final_report"] is None
    assert update["report_accepted"] is False
    assert revise_report_module.route_after_report_revision(update) == "stop_incomplete"


@pytest.mark.asyncio
async def test_synthesis_creates_cited_candidate_but_never_publishes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = [_sub_report("sq-a"), _sub_report("sq-b")]
    _, evidence = build_evidence_inventory(reports)
    unit = evidence[0]
    assert unit.source_ids
    marker = f"[E:{unit.evidence_id}] [S:{unit.source_ids[0]}]"
    outline = synthesize_module.ReportOutline(
        title="A Calibrated Analysis of Retained Research Evidence",
        executive_summary=f"Supported executive summary {marker}.",
        section_headings=["Evidence", "Implications", "Limitations"],
        key_findings=[f"Supported key finding {index} {marker}." for index in range(5)],
        limitations="Direct excerpts are bounded and do not establish universal coverage.",
    )
    outline_llm = _FakeRevisionLLM(outline)
    section_llm = _FakeRevisionLLM(f"Supported section content {marker}.")

    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        synthesize_module,
        "make_structured_llm",
        lambda *_args, **_kwargs: outline_llm,
    )
    monkeypatch.setattr(
        synthesize_module,
        "make_llm",
        lambda *_args, **_kwargs: section_llm,
    )
    monkeypatch.setattr(synthesize_module, "adispatch_custom_event", ignore_event)
    state = {
        "topic": "Synthesis candidate contract",
        "api_key": "SYNTHESIS-SECRET",
        "sub_reports": reports,
        "report_version": 0,
        "final_report": _report(99),
        "report_accepted": True,
    }

    update = await synthesize_module.synthesize_node(state)

    candidate = update["candidate_report"]
    assert candidate is not None
    assert update["final_report"] is None
    assert update["report_accepted"] is False
    assert update["report_version"] == 1
    assert all(source.source_id for source in candidate.sources)
    assert all(marker in section.content for section in candidate.sections)
    prompt = str([*outline_llm.calls, *section_llm.calls])
    assert "SYNTHESIS-SECRET" not in prompt
    assert unit.evidence_id in prompt
    assert unit.source_ids[0] in prompt
