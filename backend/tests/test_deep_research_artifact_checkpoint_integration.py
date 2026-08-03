from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from app.deep_research import artifact_recorder as recorder_module
from app.deep_research.artifact_recorder import (
    ArtifactRecordSpec,
    PostgresArtifactRecorder,
)
from app.deep_research.artifacts import (
    ArtifactAppendReceipt,
    ArtifactAppendSpec,
    DeepResearchArtifactConflictError,
    append_artifact_batch,
    canonical_payload_hash,
    get_artifact_snapshot,
)
from app.deep_research.context import (
    DEEP_RESEARCH_GRAPH_VERSION,
    DeepResearchContext,
)
from app.deep_research.graph import build_graph
from app.deep_research.models import (
    AtomicClaimAudit,
    BudgetSnapshot,
    ClaimCitationAudit,
    ClaimEvidenceReference,
    PostSynthesisEvaluation,
    PostSynthesisEvaluationRun,
    PostSynthesisRoutingDecision,
    PostSynthesisScores,
    RepairStage,
    ReportSection,
    ReportSegmentAudit,
    ResearchReport,
    SourceRef,
    SubQuestion,
    SubReport,
)
from app.deep_research.nodes.persist_artifacts import (
    ArtifactPersistenceRuntimeError,
    persist_initial_plan_node,
)
from app.deep_research.provenance import (
    build_evidence_inventory,
    build_report_segments,
    evaluation_digest,
    report_digest,
)
from app.deep_research.state import DeepResearchState
from app.models.orm import (
    DeepResearchArtifactKind,
    DeepResearchArtifactVersion,
    WorkflowRunStatus,
    WorkflowRunType,
)


RUN_ID = "d6171470-33d7-4867-a972-561cbefc7b62"
WORKSPACE_ID = "8022b64d-d489-43cd-a397-9d5188771773"
GUEST_ID = "guest-artifact-checkpoint"


def test_graph_routes_every_durable_boundary_through_persistence() -> None:
    graph = build_graph()
    assert {
        ("plan", "persist_initial_plan"),
        ("persist_initial_plan", "execute"),
        ("execute", "persist_sub_reports"),
        ("evaluate", "persist_pre_evaluation"),
        ("persist_pre_evaluation", "controller"),
        ("controller", "persist_pre_controller"),
        ("targeted_repair", "persist_repair_plan"),
        ("partial_replan", "persist_repair_plan"),
        ("full_replan", "persist_repair_plan"),
        ("synthesize", "persist_synthesis_candidate"),
        ("persist_synthesis_candidate", "evaluate_report"),
        ("evaluate_report", "persist_post_evaluation"),
        ("persist_post_evaluation", "post_controller"),
        ("post_controller", "persist_post_controller"),
        ("revise_report", "persist_revised_candidate"),
        ("finalize_complete", "persist_terminal"),
        ("finalize_incomplete", "persist_terminal"),
        ("persist_terminal", "__end__"),
    } <= graph.edges
    assert {
        "persist_sub_reports",
        "persist_pre_controller",
        "persist_repair_plan",
        "persist_post_controller",
        "persist_revised_candidate",
    } == set(graph.branches)


def _artifact(
    *,
    kind: DeepResearchArtifactKind = DeepResearchArtifactKind.plan,
    logical_id: str = "active-plan",
    version: int = 1,
    payload: dict | None = None,
    checkpoint_id: str = "checkpoint-plan",
    artifact_id: str | None = None,
) -> DeepResearchArtifactVersion:
    value = payload or {
        "plan_version": 1,
        "sub_questions": [{"id": "sq-1", "question": "Question one"}],
    }
    return DeepResearchArtifactVersion(
        id=artifact_id or str(uuid.uuid4()),
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
        artifact_kind=kind,
        logical_artifact_id=logical_id,
        version_number=version,
        plan_version=1,
        controller_cycle=0,
        schema_version=1,
        parent_version_id=None,
        source_checkpoint_id=checkpoint_id,
        content_hash=canonical_payload_hash(value),
        write_key=f"write:{kind.value}:{logical_id}:{version}",
        payload=value,
    )


def _owned_run() -> SimpleNamespace:
    return SimpleNamespace(
        id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
        run_type=WorkflowRunType.deep_research,
    )


def _append_spec(payload: dict | None = None) -> ArtifactAppendSpec:
    value = payload or {
        "plan_version": 1,
        "sub_questions": [{"id": "sq-1", "question": "Question one"}],
    }
    return ArtifactAppendSpec(
        artifact_kind=DeepResearchArtifactKind.plan,
        logical_artifact_id="active-plan",
        write_key="checkpoint:stable-plan-boundary",
        payload=value,
        plan_version=1,
        controller_cycle=0,
        source_checkpoint_id="checkpoint-plan",
        skip_if_unchanged=True,
    )


@pytest.mark.asyncio
async def test_batch_replay_lookup_precedes_next_version_allocation() -> None:
    existing = _artifact()
    existing.write_key = "checkpoint:stable-plan-boundary"
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=[_owned_run(), existing])

    receipts = await append_artifact_batch(
        db,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
        specs=[_append_spec()],
    )

    assert receipts == [
        ArtifactAppendReceipt(artifact=existing, disposition="replayed")
    ]
    assert db.scalar.await_count == 2
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_batch_skips_unchanged_logical_artifact() -> None:
    latest = _artifact()
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=[_owned_run(), None, latest])

    receipts = await append_artifact_batch(
        db,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
        specs=[_append_spec()],
    )

    assert receipts[0].artifact is latest
    assert receipts[0].disposition == "unchanged"
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_batch_replay_with_different_payload_fails_closed() -> None:
    existing = _artifact()
    existing.write_key = "checkpoint:stable-plan-boundary"
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=[_owned_run(), existing])

    with pytest.raises(DeepResearchArtifactConflictError):
        await append_artifact_batch(
            db,
            run_id=RUN_ID,
            workspace_id=WORKSPACE_ID,
            guest_id=GUEST_ID,
            specs=[_append_spec({"plan_version": 1, "sub_questions": []})],
        )

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_snapshot_hides_sub_reports_removed_from_active_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _artifact(
        payload={
            "plan_version": 2,
            "sub_questions": [{"id": "sq-active", "question": "Active"}],
        }
    )
    active = _artifact(
        kind=DeepResearchArtifactKind.sub_report,
        logical_id="sub-question:sq-active",
        payload={"sub_report": {"sub_question_id": "sq-active"}},
    )
    removed = _artifact(
        kind=DeepResearchArtifactKind.sub_report,
        logical_id="sub-question:sq-removed",
        payload={"sub_report": {"sub_question_id": "sq-removed"}},
    )

    async def fake_list(_db, **_kwargs):
        return [removed, active, plan]

    monkeypatch.setattr(
        "app.deep_research.artifacts.list_artifact_versions",
        fake_list,
    )
    snapshot = await get_artifact_snapshot(
        object(),
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
    )

    logical_ids = {item.logical_artifact_id for item in snapshot}
    assert "active-plan" in logical_ids
    assert "sub-question:sq-active" in logical_ids
    assert "sub-question:sq-removed" not in logical_ids


class _FakeRecorder:
    def __init__(self, *, fail_after_first_commit: bool = False) -> None:
        self.calls: list[dict] = []
        self.created: dict[tuple[str, str], DeepResearchArtifactVersion] = {}
        self.fail_after_first_commit = fail_after_first_commit
        self.failed_once = False

    async def record_batch(self, **kwargs):
        self.calls.append(kwargs)
        receipts = []
        for spec in kwargs["specs"]:
            key = (kwargs["source_checkpoint_id"], spec.logical_artifact_id)
            artifact = self.created.get(key)
            disposition = "replayed"
            if artifact is None:
                payload = dict(spec.payload)
                artifact = _artifact(
                    kind=DeepResearchArtifactKind(spec.artifact_kind),
                    logical_id=spec.logical_artifact_id,
                    payload=payload,
                    checkpoint_id=kwargs["source_checkpoint_id"],
                )
                self.created[key] = artifact
                disposition = "created"
            receipts.append(
                ArtifactAppendReceipt(
                    artifact=artifact,
                    disposition=disposition,
                )
            )
        if self.fail_after_first_commit and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("simulated crash after artifact commit")
        return receipts

    async def record_terminal(self, **_kwargs):
        raise AssertionError("terminal recording is outside this fixture")


def _context(recorder: _FakeRecorder, *, run_id: str = RUN_ID) -> DeepResearchContext:
    return {
        "run_id": run_id,
        "workspace_id": WORKSPACE_ID,
        "guest_id": GUEST_ID,
        "api_key": "sk-runtime-only-secret",
        "base_url": "https://private-llm.invalid/v1?token=secret",
        "model": "runtime-model",
        "graph_version": DEEP_RESEARCH_GRAPH_VERSION,
        "artifact_recorder": recorder,
        "artifact_persistence_required": True,
    }


def _checkpoint_graph(
    saver: InMemorySaver,
    recorder: _FakeRecorder,
    producer_calls: list[str],
    *,
    interrupt_after: list[str] | None = None,
):
    async def producer(_state: DeepResearchState) -> dict:
        producer_calls.append("producer")
        return {
            "sub_questions": [
                SubQuestion(
                    id="sq-1",
                    question="Question one",
                    search_queries=["question one evidence"],
                    priority=1,
                    rationale="Required for the test.",
                )
            ],
            "plan_version": 1,
        }

    builder = StateGraph(DeepResearchState, context_schema=DeepResearchContext)
    builder.add_node("producer", producer)
    builder.add_node("persist", persist_initial_plan_node)
    builder.set_entry_point("producer")
    builder.add_edge("producer", "persist")
    builder.add_edge("persist", END)
    return builder.compile(
        checkpointer=saver,
        interrupt_after=interrupt_after,
    )


@pytest.mark.asyncio
async def test_resume_runs_pending_persistence_against_producer_checkpoint() -> None:
    saver = InMemorySaver()
    recorder = _FakeRecorder()
    producer_calls: list[str] = []
    config = {"configurable": {"thread_id": RUN_ID}}

    first = _checkpoint_graph(
        saver,
        recorder,
        producer_calls,
        interrupt_after=["producer"],
    )
    await first.ainvoke({}, config=config, context=_context(recorder))
    assert producer_calls == ["producer"]
    assert recorder.calls == []

    resumed = _checkpoint_graph(saver, recorder, producer_calls)
    events = []
    async for event in resumed.astream_events(
        None,
        config=config,
        context=_context(recorder),
        version="v2",
    ):
        events.append(event)

    assert producer_calls == ["producer"]
    assert len(recorder.calls) == 1
    source_checkpoint_id = recorder.calls[0]["source_checkpoint_id"]
    source = await saver.aget_tuple(
        {
            "configurable": {
                "thread_id": RUN_ID,
                "checkpoint_id": source_checkpoint_id,
            }
        }
    )
    assert source is not None
    stored_question = source.checkpoint["channel_values"]["sub_questions"][0]
    stored_question_id = (
        stored_question.get("id")
        if isinstance(stored_question, dict)
        else stored_question.id
    )
    assert stored_question_id == "sq-1"

    artifact_events = [
        event
        for event in events
        if event.get("event") == "on_custom_event"
        and event.get("name") == "artifact_version_created"
    ]
    assert len(artifact_events) == 1
    event_payload = artifact_events[0]["data"]
    assert event_payload["source_checkpoint_id"] == source_checkpoint_id
    assert event_payload["logical_artifact_id"] == "active-plan"
    serialized = json.dumps(event_payload, sort_keys=True)
    assert "sk-runtime-only-secret" not in serialized
    assert "private-llm.invalid" not in serialized

    final_snapshot = await resumed.aget_state(config)
    checkpoint_json = json.dumps(final_snapshot.values, default=str)
    assert "sk-runtime-only-secret" not in checkpoint_json
    assert "artifact_id" not in final_snapshot.values


@pytest.mark.asyncio
async def test_crash_after_commit_replays_same_artifact_without_rerunning_producer() -> None:
    saver = InMemorySaver()
    recorder = _FakeRecorder(fail_after_first_commit=True)
    producer_calls: list[str] = []
    config = {"configurable": {"thread_id": RUN_ID}}

    first = _checkpoint_graph(saver, recorder, producer_calls)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await first.ainvoke({}, config=config, context=_context(recorder))

    resumed = _checkpoint_graph(saver, recorder, producer_calls)
    replay_events = []
    async for event in resumed.astream_events(
        None,
        config=config,
        context=_context(recorder),
        version="v2",
    ):
        replay_events.append(event)

    assert producer_calls == ["producer"]
    assert len(recorder.calls) == 2
    assert len(recorder.created) == 1
    assert (
        recorder.calls[0]["source_checkpoint_id"]
        == recorder.calls[1]["source_checkpoint_id"]
    )
    assert not any(
        event.get("event") == "on_custom_event"
        and event.get("name") == "artifact_version_created"
        for event in replay_events
    )


@pytest.mark.asyncio
async def test_checkpoint_thread_mismatch_fails_before_artifact_write() -> None:
    saver = InMemorySaver()
    recorder = _FakeRecorder()
    graph = _checkpoint_graph(saver, recorder, [])

    with pytest.raises(ArtifactPersistenceRuntimeError, match="does not match"):
        await graph.ainvoke(
            {},
            config={"configurable": {"thread_id": str(uuid.uuid4())}},
            context=_context(recorder),
        )

    assert recorder.calls == []


class _AsyncContext:
    def __init__(self, value, *, tracker: dict, transaction: bool = False):
        self.value = value
        self.tracker = tracker
        self.transaction = transaction

    async def __aenter__(self):
        if self.transaction:
            self.tracker["transaction_active"] = True
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        if self.transaction:
            self.tracker["transaction_exit_exception"] = exc_type
            self.tracker["transaction_active"] = False
        return False


@pytest.mark.asyncio
async def test_terminal_artifact_and_workflow_status_share_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker: dict[str, object] = {"transaction_active": False}
    run = SimpleNamespace(
        id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
        run_type=WorkflowRunType.deep_research,
        status=WorkflowRunStatus.running,
        current_stage="synthesizing",
        completed_at=None,
        updated_at=None,
        artifacts=None,
    )
    db = MagicMock()
    db.scalar = AsyncMock(return_value=run)
    db.flush = AsyncMock()
    db.begin = MagicMock(
        return_value=_AsyncContext(None, tracker=tracker, transaction=True)
    )

    class Factory:
        def __call__(self):
            return _AsyncContext(db, tracker=tracker)

    question = SubQuestion(
        id="sq-fixture",
        question="What does the fixture evidence establish?",
        search_queries=["fixture evidence"],
        priority=1,
        rationale="Exercise terminal reference validation.",
    )
    sub_report = SubReport(
        sub_question_id=question.id,
        question=question.question,
        findings="The fixture evidence establishes the bounded test claim.",
        key_facts=["The fixture claim is evidence bound."],
        confidence=0.95,
        gaps="This is a synthetic integration fixture.",
        sources=[
            SourceRef(
                url="https://example.com/fixture",
                title="Fixture source",
                excerpt="Fixture evidence excerpt.",
                source_type="test_fixture",
            )
        ],
    )
    sources, evidence = build_evidence_inventory([sub_report])
    source_id = sources[0].source_id
    evidence_unit = next(item for item in evidence if item.provenance == "source_excerpt")
    marker = f"[E:{evidence_unit.evidence_id}] [S:{source_id}]"
    report = ResearchReport(
        title="Accepted report",
        executive_summary=f"Evidence-bound summary. {marker}",
        sections=[ReportSection(heading="Evidence", content=f"Bounded content. {marker}")],
        key_findings=[f"Bounded finding. {marker}"],
        limitations="Fixture limitations.",
        sources=sub_report.sources,
    )
    exact_report_digest = report_digest(report)
    segment_audits = []
    for index, segment in enumerate(build_report_segments(report)):
        material = segment.component in {
            "executive_summary",
            "section",
            "key_finding",
        }
        claims = []
        if material:
            claims = [
                AtomicClaimAudit(
                    claim_id=f"claim-{index}",
                    claim_text=segment.text,
                    materiality="major",
                    support="supported",
                    evidence_refs=[
                        ClaimEvidenceReference(
                            evidence_id=evidence_unit.evidence_id,
                            supporting_excerpt=evidence_unit.text,
                        )
                    ],
                    citation=ClaimCitationAudit(
                        status="correct",
                        cited_source_ids=[source_id],
                        rationale="Fixture citation binding.",
                    ),
                    calibration="accurate",
                    rationale="Fixture claim is supported.",
                )
            ]
        segment_audits.append(
            ReportSegmentAudit(
                segment_id=segment.id,
                contains_material_claims=material,
                claims=claims,
            )
        )
    evaluation_run = PostSynthesisEvaluationRun(
        status="completed",
        evaluation=PostSynthesisEvaluation(
            schema_version="post-synthesis-eval.v1",
            rubric_version="report-quality.v1",
            segment_audits=segment_audits,
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
            summary="Accepted by the fixture evaluator.",
        ),
        error_code=None,
        report_digest=exact_report_digest,
        report_version=1,
        evaluator_model="fixture-evaluator",
        attempts=1,
        duration_ms=10,
    )
    exact_evaluation_digest = evaluation_digest(evaluation_run)
    decision = PostSynthesisRoutingDecision(
        route="accept",
        repair_stage=RepairStage.INITIAL,
        reason_code="post_quality_gate_passed",
        reason="The report passed the final quality gate.",
        affected_sub_question_ids=[],
        issue_ids=[],
        major_issue_ids=[],
        weighted_overall_score=95,
        affected_priority_ratio=0,
        score_gain=None,
        closed_major_issue_ids=[],
        fingerprint="accepted-fixture",
        escalated_from=None,
        budget=BudgetSnapshot(),
        target_report_segment_ids=[],
        report_digest=exact_report_digest,
        report_version=1,
        evaluation_digest=exact_evaluation_digest,
    )
    decision_payload = json.dumps(
        decision.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    decision_digest = hashlib.sha256(decision_payload.encode("utf-8")).hexdigest()
    candidate = _artifact(
        kind=DeepResearchArtifactKind.report_candidate,
        logical_id="candidate-report",
        payload={
            "candidate_report": report.model_dump(mode="json"),
            "report_accepted": False,
            "report_digest": exact_report_digest,
            "report_version": 1,
        },
    )
    post_eval = _artifact(
        kind=DeepResearchArtifactKind.post_synthesis_evaluation,
        logical_id="post-synthesis-evaluation",
        payload={"evaluation_run": evaluation_run.model_dump(mode="json")},
    )
    post_route = _artifact(
        kind=DeepResearchArtifactKind.controller_transition,
        logical_id="post-synthesis-controller",
        payload={
            "decision": decision.model_dump(mode="json"),
            "evaluation_digest": exact_evaluation_digest,
        },
    )
    terminal = _artifact(
        kind=DeepResearchArtifactKind.terminal_decision,
        logical_id="terminal",
    )
    observed_specs: list[ArtifactAppendSpec] = []
    plan_artifact = _artifact(
        kind=DeepResearchArtifactKind.plan,
        logical_id="active-plan",
        payload={
            "plan_version": 1,
            "sub_questions": [question.model_dump(mode="json")],
        },
    )
    sub_report_artifact = _artifact(
        kind=DeepResearchArtifactKind.sub_report,
        logical_id=f"sub-question:{question.id}",
        payload={"sub_report": sub_report.model_dump(mode="json")},
    )

    async def fake_snapshot(_db, **_kwargs):
        assert tracker["transaction_active"] is True
        return [
            plan_artifact,
            sub_report_artifact,
            candidate,
            post_eval,
            post_route,
        ]

    async def fake_append(_db, *, specs, **_kwargs):
        assert tracker["transaction_active"] is True
        assert run.status == WorkflowRunStatus.running
        observed_specs.extend(specs)
        return [ArtifactAppendReceipt(terminal, "created")]

    monkeypatch.setattr(recorder_module, "get_artifact_snapshot", fake_snapshot)
    monkeypatch.setattr(recorder_module, "append_artifact_batch", fake_append)

    recorder = PostgresArtifactRecorder(Factory())
    receipt = await recorder.record_terminal(
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        guest_id=GUEST_ID,
        source_checkpoint_id="checkpoint-terminal",
        graph_version=DEEP_RESEARCH_GRAPH_VERSION,
        producer_node="finalize_terminal",
        spec=ArtifactRecordSpec(
            artifact_kind=DeepResearchArtifactKind.terminal_decision,
            logical_artifact_id="terminal",
            payload={
                "terminal_status": "completed",
                "report_accepted": True,
                "report_version": 1,
                "report_digest": exact_report_digest,
                "post_evaluation_digest": exact_evaluation_digest,
                "controller_decision_digest": decision_digest,
            },
            plan_version=1,
            controller_cycle=2,
            expected_version_number=1,
            singleton=True,
        ),
        status=WorkflowRunStatus.completed,
    )

    assert receipt.artifact is terminal
    assert run.status == WorkflowRunStatus.completed
    assert run.completed_at is not None
    assert run.artifacts["terminal_artifact_id"] == terminal.id
    assert run.artifacts["report_artifact_id"] == candidate.id
    assert observed_specs[0].payload["artifact_refs"]["report_candidate"][
        "artifact_id"
    ] == candidate.id
    assert tracker["transaction_exit_exception"] is None
    assert tracker["transaction_active"] is False

    low_scores = evaluation_run.evaluation.scores.model_copy(
        update={"material_claim_grounding": 10}
    )
    low_evaluation = evaluation_run.model_copy(
        update={
            "evaluation": evaluation_run.evaluation.model_copy(
                update={"scores": low_scores}
            )
        }
    )
    low_evaluation_artifact = _artifact(
        kind=DeepResearchArtifactKind.post_synthesis_evaluation,
        logical_id="post-synthesis-evaluation",
        payload={"evaluation_run": low_evaluation.model_dump(mode="json")},
    )
    low_evaluation_digest = evaluation_digest(low_evaluation)
    low_decision = decision.model_copy(
        update={
            "evaluation_digest": low_evaluation_digest,
            "weighted_overall_score": 78.0,
        }
    )
    low_decision_payload = json.dumps(
        low_decision.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    low_route_artifact = _artifact(
        kind=DeepResearchArtifactKind.controller_transition,
        logical_id="post-synthesis-controller",
        payload={
            "decision": low_decision.model_dump(mode="json"),
            "evaluation_digest": low_evaluation_digest,
        },
    )
    terminal_payload = {
        "terminal_status": "completed",
        "report_accepted": True,
        "report_version": 1,
        "report_digest": exact_report_digest,
        "post_evaluation_digest": low_evaluation_digest,
        "controller_decision_digest": hashlib.sha256(
            low_decision_payload.encode("utf-8")
        ).hexdigest(),
    }
    with pytest.raises(
        DeepResearchArtifactConflictError,
        match="lineage is inconsistent",
    ):
        recorder_module._validate_completed_terminal_lineage(
            terminal_payload=terminal_payload,
            candidate_artifact=candidate,
            evaluation_artifact=low_evaluation_artifact,
            controller_artifact=low_route_artifact,
            active_questions=[question],
            active_reports=[sub_report],
        )

    forged_audits = list(evaluation_run.evaluation.segment_audits)
    forged_index = next(
        index for index, audit in enumerate(forged_audits) if audit.claims
    )
    forged_claim = forged_audits[forged_index].claims[0].model_copy(
        update={
            "evidence_refs": [
                ClaimEvidenceReference(
                    evidence_id="ev-forged-reference",
                    supporting_excerpt=evidence_unit.text,
                )
            ]
        }
    )
    forged_audits[forged_index] = forged_audits[forged_index].model_copy(
        update={"claims": [forged_claim]}
    )
    forged_evaluation = evaluation_run.model_copy(
        update={
            "evaluation": evaluation_run.evaluation.model_copy(
                update={"segment_audits": forged_audits}
            )
        }
    )
    forged_evaluation_digest = evaluation_digest(forged_evaluation)
    forged_decision = decision.model_copy(
        update={"evaluation_digest": forged_evaluation_digest}
    )
    forged_decision_payload = json.dumps(
        forged_decision.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with pytest.raises(
        DeepResearchArtifactConflictError,
        match="lineage is inconsistent",
    ):
        recorder_module._validate_completed_terminal_lineage(
            terminal_payload={
                "terminal_status": "completed",
                "report_accepted": True,
                "report_version": 1,
                "report_digest": exact_report_digest,
                "post_evaluation_digest": forged_evaluation_digest,
                "controller_decision_digest": hashlib.sha256(
                    forged_decision_payload.encode("utf-8")
                ).hexdigest(),
            },
            candidate_artifact=candidate,
            evaluation_artifact=_artifact(
                kind=DeepResearchArtifactKind.post_synthesis_evaluation,
                logical_id="post-synthesis-evaluation",
                payload={
                    "evaluation_run": forged_evaluation.model_dump(mode="json")
                },
            ),
            controller_artifact=_artifact(
                kind=DeepResearchArtifactKind.controller_transition,
                logical_id="post-synthesis-controller",
                payload={
                    "decision": forged_decision.model_dump(mode="json"),
                    "evaluation_digest": forged_evaluation_digest,
                },
            ),
            active_questions=[question],
            active_reports=[sub_report],
        )

    noninitial_decision = decision.model_copy(
        update={"repair_stage": RepairStage.EVIDENCE}
    )
    noninitial_payload = json.dumps(
        noninitial_decision.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    noninitial_route = _artifact(
        kind=DeepResearchArtifactKind.controller_transition,
        logical_id="post-synthesis-controller",
        payload={
            "decision": noninitial_decision.model_dump(mode="json"),
            "evaluation_digest": exact_evaluation_digest,
        },
    )
    with pytest.raises(
        DeepResearchArtifactConflictError,
        match="lineage is inconsistent",
    ):
        recorder_module._validate_completed_terminal_lineage(
            terminal_payload={
                **terminal_payload,
                "post_evaluation_digest": exact_evaluation_digest,
                "controller_decision_digest": hashlib.sha256(
                    noninitial_payload.encode("utf-8")
                ).hexdigest(),
            },
            candidate_artifact=candidate,
            evaluation_artifact=post_eval,
            controller_artifact=noninitial_route,
            active_questions=[question],
            active_reports=[sub_report],
        )


@pytest.mark.asyncio
async def test_completed_terminal_rejects_unbound_artifact_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker: dict[str, object] = {"transaction_active": False}
    run = SimpleNamespace(
        status=WorkflowRunStatus.running,
        current_stage="synthesizing",
        completed_at=None,
        updated_at=None,
        artifacts=None,
    )
    db = MagicMock()
    db.scalar = AsyncMock(return_value=run)
    db.flush = AsyncMock()
    db.begin = MagicMock(
        return_value=_AsyncContext(None, tracker=tracker, transaction=True)
    )

    class Factory:
        def __call__(self):
            return _AsyncContext(db, tracker=tracker)

    async def fake_snapshot(_db, **_kwargs):
        return [
            _artifact(
                kind=DeepResearchArtifactKind.report_candidate,
                logical_id="candidate-report",
            ),
            _artifact(
                kind=DeepResearchArtifactKind.post_synthesis_evaluation,
                logical_id="post-synthesis-evaluation",
            ),
            _artifact(
                kind=DeepResearchArtifactKind.controller_transition,
                logical_id="post-synthesis-controller",
            ),
        ]

    append = AsyncMock()
    monkeypatch.setattr(recorder_module, "get_artifact_snapshot", fake_snapshot)
    monkeypatch.setattr(recorder_module, "append_artifact_batch", append)

    with pytest.raises(
        DeepResearchArtifactConflictError,
        match="active research plan",
    ):
        await PostgresArtifactRecorder(Factory()).record_terminal(
            run_id=RUN_ID,
            workspace_id=WORKSPACE_ID,
            guest_id=GUEST_ID,
            source_checkpoint_id="checkpoint-terminal",
            graph_version=DEEP_RESEARCH_GRAPH_VERSION,
            producer_node="finalize_terminal",
            spec=ArtifactRecordSpec(
                artifact_kind=DeepResearchArtifactKind.terminal_decision,
                logical_artifact_id="terminal",
                payload={
                    "terminal_status": "completed",
                    "report_accepted": True,
                    "report_version": 1,
                    "report_digest": "a" * 64,
                    "post_evaluation_digest": "b" * 64,
                    "controller_decision_digest": "c" * 64,
                },
                plan_version=1,
                controller_cycle=2,
                expected_version_number=1,
                singleton=True,
            ),
            status=WorkflowRunStatus.completed,
        )

    append.assert_not_awaited()
    assert run.status == WorkflowRunStatus.running
