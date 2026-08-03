"""API publication and console-event tests for the Deep Research quality gate."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.api import deep_research as api
from app.deep_research.models import (
    BudgetSnapshot,
    PostSynthesisEvaluation,
    PostSynthesisEvaluationRun,
    PostSynthesisRoutingDecision,
    PostSynthesisScores,
    RepairStage,
    ReportSegmentAudit,
    ResearchReport,
    ReportSection,
    SourceRef,
)
from app.deep_research.events import (
    DEEP_RESEARCH_EVENT_SCHEMA_VERSION,
    validate_run_event_payload,
)
from app.deep_research.provenance import evaluation_digest, report_digest


def _report(*, marker: str = "accepted") -> ResearchReport:
    return ResearchReport(
        title=f"{marker.title()} research report",
        executive_summary=f"Summary for {marker}.",
        sections=[ReportSection(heading="Findings", content=f"Evidence for {marker}.")],
        key_findings=[f"Finding for {marker}."],
        limitations="The evidence base is bounded.",
        sources=[SourceRef(url="https://example.test/source", title="Source")],
    )


def _request() -> api.DeepResearchRequest:
    return api.DeepResearchRequest(
        input=api.DeepResearchInput(topic="Specific research question with context"),
        workspace_id="workspace-1",
    )


def _accepted_quality_state(report: ResearchReport) -> dict[str, Any]:
    digest = report_digest(report)
    evaluation = PostSynthesisEvaluation(
        schema_version="post-synthesis-eval.v1",
        rubric_version="report-quality.v1",
        segment_audits=[
            ReportSegmentAudit(
                segment_id="section-0",
                contains_material_claims=False,
                claims=[],
            )
        ],
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
        issues=[],
        unresolved_questions=[],
        summary="The candidate passed the bounded report audit.",
    )
    run = PostSynthesisEvaluationRun(
        status="completed",
        evaluation=evaluation,
        error_code=None,
        report_digest=digest,
        report_version=1,
        evaluator_model="post-judge",
        attempts=1,
        duration_ms=1200,
    )
    decision = PostSynthesisRoutingDecision(
        route="accept",
        repair_stage=RepairStage.INITIAL,
        reason_code="post_quality_gate_passed",
        reason="All deterministic post-synthesis gates passed.",
        affected_sub_question_ids=[],
        issue_ids=[],
        major_issue_ids=[],
        weighted_overall_score=95,
        affected_priority_ratio=0,
        score_gain=None,
        closed_major_issue_ids=[],
        fingerprint="a" * 64,
        escalated_from=None,
        budget=BudgetSnapshot(post_evaluations_used=1),
        target_report_segment_ids=[],
        report_digest=digest,
        report_version=1,
        evaluation_digest=evaluation_digest(run),
    )
    return {
        "candidate_report": report,
        "final_report": report,
        "report_accepted": True,
        "terminal_status": "completed",
        "report_version": 1,
        "post_synthesis_evaluation_run": run,
        "post_synthesis_controller_decision": decision,
        "controller_decision": decision,
    }


class _FakeRuntime:
    def __init__(self, result: dict[str, Any] | None = None, error: Exception | None = None):
        self.result = result or {}
        self.error = error
        self.backend = "memory"

    async def astream_events(self, _state, _run_id, _context, callbacks=None):
        del callbacks
        if self.error is not None:
            raise self.error
        if False:
            yield {}

    async def aget_state(self, _run_id):
        return SimpleNamespace(values=self.result)


class _FakeStreamingRuntime(_FakeRuntime):
    def __init__(self, events: list[dict[str, Any]], result: dict[str, Any]):
        super().__init__(result=result)
        self.events = events

    async def astream_events(self, _state, _run_id, context, callbacks=None):
        del callbacks
        assert context["api_key"] == "server-only-key"
        for event in self.events:
            yield event


async def _allow_workspace(_workspace_id: str, _guest_id: str) -> None:
    return None


async def _create_same_run(**kwargs) -> str:
    return kwargs["run_id"]


async def _accept_terminal_persistence(**_kwargs) -> None:
    return None


async def _accept_stage_update(*_args, **_kwargs) -> None:
    return None


@asynccontextmanager
async def _no_op_run_lock(_run_id: str):
    yield


def _patch_durable_api_seams(monkeypatch, runtime: _FakeRuntime) -> None:
    event_seq = 0

    async def append_event(self, event_type, payload, *, boundary, checkpoint_id=None):
        nonlocal event_seq
        validate_run_event_payload(event_type, payload)
        event_seq += 1
        report_version = self.state.get("report_version")
        if not isinstance(report_version, int) or report_version < 1:
            report_version = None
        return SimpleNamespace(
            schema_version=DEEP_RESEARCH_EVENT_SCHEMA_VERSION,
            event_id=self.event_id(event_type, boundary),
            seq=event_seq,
            type=event_type,
            run_id=self.run_id,
            emitted_at=datetime.now(timezone.utc),
            cycle=0,
            plan_version=max(1, int(self.state.get("plan_version") or 1)),
            corpus_version=max(0, int(self.state.get("corpus_version") or 0)),
            report_version=report_version,
            checkpoint_id=checkpoint_id or self.checkpoint_id,
            payload=dict(payload),
        )

    async def no_artifacts(**_kwargs):
        return []

    monkeypatch.setattr(api, "_require_owned_workspace", _allow_workspace)
    monkeypatch.setattr(api, "create_workflow_run", _create_same_run)
    monkeypatch.setattr(
        api,
        "_verify_graph_terminal_persisted",
        _accept_terminal_persistence,
    )
    monkeypatch.setattr(api, "_record_execution_stop", _accept_terminal_persistence)
    monkeypatch.setattr(api, "update_workflow_stage", _accept_stage_update)
    monkeypatch.setattr(
        api,
        "_committed_terminal_result_or_none",
        _accept_terminal_persistence,
    )
    monkeypatch.setattr(api, "deep_research_run_lock", _no_op_run_lock)
    monkeypatch.setattr(api, "get_deep_research_runtime", lambda: runtime)
    monkeypatch.setattr(api.DurableRunEventWriter, "append", append_event)
    monkeypatch.setattr(api, "_owned_artifacts", no_artifacts)


async def _fake_llm() -> SimpleNamespace:
    return SimpleNamespace(
        resolved=SimpleNamespace(
            api_key="server-only-key",
            base_url="https://llm.example.test",
            model="evaluator-model",
        )
    )


@pytest.mark.parametrize(
    ("state_update", "publishable"),
    [
        ({"terminal_status": "completed", "report_accepted": True}, True),
        ({"terminal_status": "incomplete", "report_accepted": True}, False),
        ({"terminal_status": "completed", "report_accepted": False}, False),
        ({"terminal_status": "completed", "report_accepted": True, "final_report": None}, False),
    ],
)
def test_publication_requires_completed_accepted_report(
    state_update: dict[str, Any],
    publishable: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api,
        "post_synthesis_evaluation_is_acceptable",
        lambda _state: True,
    )
    state = {**_accepted_quality_state(_report()), **state_update}
    assert (api._publishable_report(state) is not None) is publishable


def test_stream_state_only_becomes_publishable_after_finalize_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api,
        "post_synthesis_evaluation_is_acceptable",
        lambda _state: True,
    )
    rejected = _report(marker="rejected")
    accepted_state = _accepted_quality_state(rejected)
    observed: dict[str, Any] = {"report_accepted": False}

    api._capture_node_output(
        observed,
        {
            "candidate_report": rejected,
            "report_accepted": False,
            "post_synthesis_evaluation_run": accepted_state["post_synthesis_evaluation_run"],
            "post_synthesis_controller_decision": accepted_state[
                "post_synthesis_controller_decision"
            ],
            "report_version": accepted_state["report_version"],
        },
    )
    assert api._publishable_report(observed) is None

    api._capture_node_output(
        observed,
        {
            "final_report": rejected,
            "report_accepted": True,
            "terminal_status": "completed",
            "terminal_reason": None,
        },
    )
    assert api._publishable_report(observed) == rejected


def test_incomplete_result_exposes_diagnostics_not_candidate_content_or_keys() -> None:
    candidate = _report(marker="sk-super-secret-candidate")
    state = {
        "terminal_status": "incomplete",
        "terminal_reason": "authorization: Bearer sk-super-secret",
        "workflow_error_code": "post_quality_gate_failed",
        "candidate_report": candidate,
        "report_accepted": False,
        "post_synthesis_evaluation_run": {
            "status": "completed",
            "evaluation": {
                "scores": {"material_claim_grounding": 61},
                "issues": [
                    {
                        "id": "issue-1",
                        "category": "unsupported_claim",
                        "severity": "major",
                        "description": "Bearer sk-nested-secret appears in rejected prose.",
                        "acceptance_criteria": ["Repeat the rejected private claim."],
                        "source_urls": ["https://private.example.test/internal"],
                    }
                ],
            },
            "error_code": None,
            "evaluator_model": "judge-model",
            "attempts": 1,
            "duration_ms": 1234,
        },
        "post_synthesis_controller_decision": {
            "route": "stop_incomplete",
            "repair_stage": "initial",
            "affected_sub_question_ids": ["sq-1"],
            "target_report_segment_ids": ["section-0"],
            "reason_code": "budget_exhausted",
            "reason": "The bounded repair budget is exhausted.",
            "budget": {"total_recoveries_used": 4},
        },
    }

    result = api._incomplete_result(state, "run-1")
    serialized = json.dumps(result.model_dump(mode="json"))

    assert result.status == "incomplete"
    assert result.terminal_reason == "The candidate report did not pass the research quality gate."
    assert result.candidate_diagnostics == {
        "terminal_status": "incomplete",
        "report_accepted": False,
        "candidate_report": {"present": True, "section_count": 1, "source_count": 1},
        "workflow_error_code": "post_quality_gate_failed",
        "last_evaluation": {
            "phase": "post_synthesis",
            "status": "completed",
            "model": "judge-model",
            "duration_ms": 1234,
            "attempts": 1,
            "error_code": None,
            "scores": {"material_claim_grounding": 61},
            "issues": [
                {
                    "id": "issue-1",
                    "category": "unsupported_claim",
                    "severity": "major",
                    "source_count": 1,
                }
            ],
            "issue_count": 1,
        },
        "last_route": {
            "phase": "post_synthesis",
            "route": "stop_incomplete",
            "repair_stage": "initial",
            "targets": {
                "sub_question_ids": ["sq-1"],
                "report_segment_ids": ["section-0"],
            },
            "target_sub_question_ids": ["sq-1"],
            "target_report_segment_ids": ["section-0"],
            "reason_code": "budget_exhausted",
            "reason": "The bounded repair budget is exhausted.",
            "budgets": {"total_recoveries_used": 4},
            "weighted_overall_score": None,
        },
    }
    assert "sk-super-secret" not in serialized
    assert "sk-nested-secret" not in serialized
    assert "private.example.test" not in serialized
    assert "Evidence for sk-super-secret-candidate" not in serialized
    assert "server-only-key" not in serialized


def test_evaluator_and_route_events_are_derived_from_typed_node_outputs() -> None:
    evaluator = api._evaluation_event_payload(
        {
            "pre_synthesis_evaluation_run": {
                "status": "completed",
                "evaluation": {
                    "scores": {"intent_alignment": 91},
                    "issues": [{"id": "coverage-1", "severity": "major"}],
                },
                "error_code": None,
                "evaluator_model": "judge-v2",
                "attempts": 2,
                "duration_ms": 4200,
            }
        },
        phase="pre_synthesis",
    )
    route = api._route_event_payload(
        {
            "controller_decision": {
                "route": "targeted_repair",
                "repair_stage": "targeted_repair",
                "affected_sub_question_ids": ["sq-2"],
                "reason_code": "localized_evidence_failure",
                "reason": "One evidence branch needs repair.",
                "budget": {"targeted_repairs_used": 1},
                "weighted_overall_score": 79.5,
            }
        },
        phase="pre_synthesis",
    )

    assert evaluator == {
        "phase": "pre_synthesis",
        "status": "completed",
        "model": "judge-v2",
        "duration_ms": 4200,
        "attempts": 2,
        "error_code": None,
        "scores": {"intent_alignment": 91},
        "issues": [{"id": "coverage-1", "severity": "major"}],
        "issue_count": 1,
    }
    assert route is not None
    assert route["route"] == "targeted_repair"
    assert route["targets"]["sub_question_ids"] == ["sq-2"]
    assert route["budgets"] == {"targeted_repairs_used": 1}


def test_route_event_rejects_non_authoritative_sixth_route() -> None:
    assert api._route_event_payload(
        {"controller_decision": {"route": "revise_synthesis"}},
        phase="pre_synthesis",
    ) is None


def test_route_event_rejects_a_decision_from_the_wrong_evaluation_phase() -> None:
    assert api._route_event_payload(
        {
            "controller_decision": {
                "route": "targeted_repair",
                "evaluation_phase": "post_synthesis",
            }
        },
        phase="pre_synthesis",
    ) is None


def test_stage_map_covers_the_quality_and_repair_loop() -> None:
    assert {
        "controller",
        "targeted_repair",
        "partial_replan",
        "full_replan",
        "evaluate_report",
        "post_controller",
        "revise_report",
        "finalize_complete",
        "finalize_incomplete",
    }.issubset(api._NODE_STAGE_MAP)


@pytest.mark.asyncio
async def test_batch_rejected_candidate_returns_incomplete_with_full_uuid(monkeypatch) -> None:
    candidate = _report(marker="rejected")
    monkeypatch.setattr(api, "_resolve_llm", lambda _guest_id: _fake_llm())

    async def no_clarifications(_req, _llm):
        return []

    monkeypatch.setattr(api, "_llm_validate_topic", no_clarifications)
    _patch_durable_api_seams(
        monkeypatch,
        _FakeRuntime(
            result={
                "candidate_report": candidate,
                "final_report": None,
                "report_accepted": False,
                "terminal_status": "incomplete",
                "terminal_reason": "A material claim remains unsupported.",
            },
        ),
    )

    result = await api.run_deep_research(_request(), guest_id="guest-1")

    assert result.status == "incomplete"
    assert result.generated_title is None
    assert result.section_updates == []
    assert result.terminal_reason == "A material claim remains unsupported."
    assert uuid.UUID(result.run_id).version == 4


@pytest.mark.asyncio
async def test_batch_graph_exception_does_not_expose_raw_error(monkeypatch) -> None:
    monkeypatch.setattr(api, "_resolve_llm", lambda _guest_id: _fake_llm())

    async def no_clarifications(_req, _llm):
        return []

    monkeypatch.setattr(api, "_llm_validate_topic", no_clarifications)
    _patch_durable_api_seams(
        monkeypatch,
        _FakeRuntime(error=RuntimeError("Bearer sk-raw-secret")),
    )

    result = await api.run_deep_research(_request(), guest_id="guest-1")

    assert result.status == "failed"
    assert "sk-raw-secret" not in (result.message or "")
    assert result.message == "Research could not be completed. Please try again."


@pytest.mark.asyncio
async def test_stream_emits_authoritative_events_and_never_publishes_rejected_candidate(
    monkeypatch,
) -> None:
    candidate = _report(marker="rejected-stream")
    post_run = {
        "status": "completed",
        "evaluation": {
            "scores": {"citation_completeness": 40},
            "issues": [{"id": "citation-1", "severity": "major"}],
        },
        "error_code": None,
        "evaluator_model": "post-judge",
        "attempts": 1,
        "duration_ms": 2200,
    }
    decision = {
        "route": "stop_incomplete",
        "repair_stage": "initial",
        "affected_sub_question_ids": ["sq-1"],
        "target_report_segment_ids": ["section-0"],
        "reason_code": "post_repair_budget_exhausted",
        "reason": "The report still has unsupported material claims.",
        "budget": {"post_evaluations_used": 3},
    }
    events = [
        {
            "event": "on_chain_start",
            "name": "evaluate_report",
            "data": {},
        },
        {
            "event": "on_chain_end",
            "name": "evaluate_report",
            "data": {"output": {"post_synthesis_evaluation_run": post_run}},
        },
        {
            "event": "on_chain_start",
            "name": "post_controller",
            "data": {},
        },
        {
            "event": "on_chain_end",
            "name": "post_controller",
            "data": {"output": {"post_synthesis_controller_decision": decision}},
        },
        {
            "event": "on_chain_end",
            "name": "synthesize",
            "data": {
                "output": {
                    "candidate_report": candidate,
                    "report_accepted": False,
                }
            },
        },
        {
            "event": "on_chain_start",
            "name": "finalize_incomplete",
            "data": {},
        },
        {
            "event": "on_chain_end",
            "name": "finalize_incomplete",
            "data": {
                "output": {
                    "final_report": None,
                    "report_accepted": False,
                    "terminal_status": "incomplete",
                    "terminal_reason": "The report still has unsupported material claims.",
                }
            },
        },
    ]

    monkeypatch.setattr(api, "_resolve_llm", lambda _guest_id: _fake_llm())

    async def no_clarifications(_req, _llm):
        return []

    monkeypatch.setattr(api, "_llm_validate_topic", no_clarifications)
    monkeypatch.setattr(api, "create_trace", lambda **_kwargs: None)
    monkeypatch.setattr(api, "get_langfuse_callback_handler", lambda _trace: None)
    _patch_durable_api_seams(
        monkeypatch,
        _FakeStreamingRuntime(
            events,
            result={
                "candidate_report": candidate,
                "final_report": None,
                "report_accepted": False,
                "terminal_status": "incomplete",
                "terminal_reason": "The report still has unsupported material claims.",
                "post_synthesis_evaluation_run": post_run,
                "post_synthesis_controller_decision": decision,
            },
        ),
    )

    endpoint = getattr(api.run_deep_research_stream, "__wrapped__", api.run_deep_research_stream)
    response = await endpoint(request=None, req=_request(), guest_id="guest-1")
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in "".join(chunks).splitlines()
        if line.startswith("data: ")
    ]

    result_event = next(event for event in payloads if event["type"] == "run_finished")
    serialized = json.dumps(payloads)

    assert payloads[0]["type"] == "run_started"
    assert result_event["payload"]["status"] == "incomplete"
    assert (
        result_event["payload"]["terminal_reason"]
        == "The report still has unsupported material claims."
    )
    assert not any(
        event.get("type") == "run_finished"
        and event.get("payload", {}).get("status") == "completed"
        for event in payloads
    )
    assert "Evidence for rejected-stream" not in serialized
