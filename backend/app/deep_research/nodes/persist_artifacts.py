"""Explicit durable boundaries between graph outputs and application artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from langchain_core.callbacks import adispatch_custom_event
from langgraph.runtime import Runtime
from pydantic import BaseModel

from app.deep_research.artifact_recorder import (
    ArtifactRecordSpec,
    ArtifactRecorder,
)
from app.deep_research.context import (
    DEEP_RESEARCH_GRAPH_VERSION,
    DeepResearchContext,
)
from app.deep_research.models import (
    PostSynthesisEvaluationRun,
    PostSynthesisRoutingDecision,
    PreSynthesisEvaluationRun,
    ResearchReport,
    RoutingDecision,
    SubQuestion,
    SubReport,
)
from app.deep_research.provenance import report_digest
from app.deep_research.state import DeepResearchState
from app.models.orm import DeepResearchArtifactKind, WorkflowRunStatus


class ArtifactPersistenceRuntimeError(RuntimeError):
    """Runtime identity/checkpoint lineage is absent or inconsistent."""


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_value,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plan_version(state: DeepResearchState) -> int:
    value = state.get("plan_version", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("artifact persistence requires a positive plan version")
    return value


def _controller_cycle(state: DeepResearchState) -> int:
    pre = state.get("routing_history", [])
    post = state.get("post_routing_history", [])
    if not isinstance(pre, list) or not isinstance(post, list):
        raise ValueError("routing histories must be lists")
    return len(pre) + len(post)


def _model(value: Any, model_type: type[BaseModel]) -> BaseModel:
    if isinstance(value, model_type):
        return value
    return model_type.model_validate(value)


def _runtime_boundary(
    runtime: Runtime[DeepResearchContext] | None,
) -> tuple[DeepResearchContext, ArtifactRecorder, str] | None:
    context = runtime.context if runtime is not None else None
    if not isinstance(context, dict):
        return None
    required = bool(context.get("artifact_persistence_required", False))
    recorder = context.get("artifact_recorder")
    if recorder is None:
        if required:
            raise ArtifactPersistenceRuntimeError(
                "artifact persistence is required but no recorder was provided"
            )
        return None
    if context.get("graph_version") != DEEP_RESEARCH_GRAPH_VERSION:
        raise ArtifactPersistenceRuntimeError("artifact graph version mismatch")

    info = runtime.execution_info
    if info is None or not info.checkpoint_id or not info.thread_id:
        raise ArtifactPersistenceRuntimeError(
            "artifact persistence requires checkpoint execution metadata"
        )
    if info.thread_id != context.get("run_id"):
        raise ArtifactPersistenceRuntimeError(
            "checkpoint thread does not match the Deep Research run"
        )
    return context, recorder, info.checkpoint_id


async def _emit_receipts(receipts) -> None:
    for receipt in receipts:
        if receipt.disposition != "created":
            continue
        artifact = receipt.artifact
        kind = artifact.artifact_kind
        await adispatch_custom_event(
            "artifact_version_created",
            {
                "artifact_id": artifact.id,
                "artifact_kind": kind.value if hasattr(kind, "value") else str(kind),
                "logical_artifact_id": artifact.logical_artifact_id,
                "version_number": artifact.version_number,
                "content_hash": artifact.content_hash,
                "source_checkpoint_id": artifact.source_checkpoint_id,
                "plan_version": artifact.plan_version,
                "controller_cycle": artifact.controller_cycle,
            },
        )


async def _record(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None,
    *,
    producer_node: str,
    specs: list[ArtifactRecordSpec],
) -> dict:
    boundary = _runtime_boundary(runtime)
    if boundary is None or not specs:
        return {}
    context, recorder, checkpoint_id = boundary
    receipts = await recorder.record_batch(
        run_id=context["run_id"],
        workspace_id=context["workspace_id"],
        guest_id=context["guest_id"],
        source_checkpoint_id=checkpoint_id,
        graph_version=context["graph_version"],
        producer_node=producer_node,
        specs=specs,
    )
    await _emit_receipts(receipts)
    return {}


def _plan_spec(state: DeepResearchState) -> ArtifactRecordSpec | None:
    raw_questions = state.get("sub_questions", [])
    if not isinstance(raw_questions, list) or not raw_questions:
        return None
    questions = [
        _model(item, SubQuestion).model_dump(mode="json")
        for item in raw_questions
    ]
    return ArtifactRecordSpec(
        artifact_kind=DeepResearchArtifactKind.plan,
        logical_artifact_id="active-plan",
        payload={
            "plan_version": _plan_version(state),
            "sub_questions": questions,
        },
        plan_version=_plan_version(state),
        controller_cycle=_controller_cycle(state),
        skip_if_unchanged=True,
    )


async def persist_initial_plan_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    if _runtime_boundary(runtime) is None:
        return {}
    spec = _plan_spec(state)
    return await _record(
        state,
        runtime,
        producer_node="plan",
        specs=[spec] if spec is not None else [],
    )


async def persist_repair_plan_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    if _runtime_boundary(runtime) is None:
        return {}
    if state.get("repair_preparation_status") != "ready":
        return {}
    spec = _plan_spec(state)
    return await _record(
        state,
        runtime,
        producer_node="repair_plan",
        specs=[spec] if spec is not None else [],
    )


async def persist_sub_reports_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    if _runtime_boundary(runtime) is None:
        return {}
    if state.get("execute_status") != "completed":
        return {}
    raw_reports = state.get("sub_reports", [])
    if not isinstance(raw_reports, list):
        raise ValueError("sub_reports must be a list")
    reports = [_model(item, SubReport) for item in raw_reports]
    report_ids = [report.sub_question_id for report in reports]
    if len(report_ids) != len(set(report_ids)):
        raise ValueError("sub_reports must have unique sub-question IDs")
    specs = [
        ArtifactRecordSpec(
            artifact_kind=DeepResearchArtifactKind.sub_report,
            logical_artifact_id=f"sub-question:{report.sub_question_id}",
            payload={"sub_report": report.model_dump(mode="json")},
            plan_version=_plan_version(state),
            controller_cycle=_controller_cycle(state),
            skip_if_unchanged=True,
        )
        for report in reports
    ]
    return await _record(
        state,
        runtime,
        producer_node="execute",
        specs=specs,
    )


async def persist_pre_evaluation_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    if _runtime_boundary(runtime) is None:
        return {}
    raw_run = state.get("pre_synthesis_evaluation_run")
    if raw_run is None:
        return {}
    run = _model(raw_run, PreSynthesisEvaluationRun)
    corpus = {
        "sub_questions": [
            _model(item, SubQuestion).model_dump(mode="json")
            for item in state.get("sub_questions", [])
        ],
        "sub_reports": [
            _model(item, SubReport).model_dump(mode="json")
            for item in state.get("sub_reports", [])
        ],
    }
    spec = ArtifactRecordSpec(
        artifact_kind=DeepResearchArtifactKind.pre_synthesis_evaluation,
        logical_artifact_id="pre-synthesis-evaluation",
        payload={
            "corpus_digest": _stable_digest(corpus),
            "evaluation_run": run.model_dump(mode="json"),
        },
        plan_version=_plan_version(state),
        controller_cycle=_controller_cycle(state),
    )
    return await _record(
        state,
        runtime,
        producer_node="evaluate",
        specs=[spec],
    )


async def persist_pre_controller_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    if _runtime_boundary(runtime) is None:
        return {}
    raw_decision = state.get("controller_decision")
    if raw_decision is None:
        return {}
    decision = _model(raw_decision, RoutingDecision)
    raw_evaluation = state.get("pre_synthesis_evaluation_run")
    evaluation_digest = (
        _stable_digest(_model(raw_evaluation, PreSynthesisEvaluationRun))
        if raw_evaluation is not None
        else None
    )
    spec = ArtifactRecordSpec(
        artifact_kind=DeepResearchArtifactKind.controller_transition,
        logical_artifact_id="pre-synthesis-controller",
        payload={
            "decision": decision.model_dump(mode="json"),
            "evaluation_digest": evaluation_digest,
        },
        plan_version=_plan_version(state),
        controller_cycle=_controller_cycle(state),
    )
    return await _record(
        state,
        runtime,
        producer_node="controller",
        specs=[spec],
    )


def _candidate_spec(state: DeepResearchState) -> ArtifactRecordSpec | None:
    raw_report = state.get("candidate_report")
    if raw_report is None:
        return None
    report = _model(raw_report, ResearchReport)
    version = state.get("report_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("candidate artifact requires a positive report version")
    return ArtifactRecordSpec(
        artifact_kind=DeepResearchArtifactKind.report_candidate,
        logical_artifact_id="candidate-report",
        payload={
            "candidate_report": report.model_dump(mode="json"),
            "report_accepted": False,
            "report_digest": report_digest(report),
            "report_version": version,
        },
        plan_version=_plan_version(state),
        controller_cycle=_controller_cycle(state),
        expected_version_number=version,
    )


async def persist_synthesis_candidate_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    if _runtime_boundary(runtime) is None:
        return {}
    spec = _candidate_spec(state)
    return await _record(
        state,
        runtime,
        producer_node="synthesize",
        specs=[spec] if spec is not None else [],
    )


async def persist_revised_candidate_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    if _runtime_boundary(runtime) is None:
        return {}
    if state.get("report_revision_status") != "completed":
        return {}
    spec = _candidate_spec(state)
    return await _record(
        state,
        runtime,
        producer_node="revise_report",
        specs=[spec] if spec is not None else [],
    )


async def persist_post_evaluation_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    if _runtime_boundary(runtime) is None:
        return {}
    raw_run = state.get("post_synthesis_evaluation_run")
    if raw_run is None:
        return {}
    run = _model(raw_run, PostSynthesisEvaluationRun)
    spec = ArtifactRecordSpec(
        artifact_kind=DeepResearchArtifactKind.post_synthesis_evaluation,
        logical_artifact_id="post-synthesis-evaluation",
        payload={"evaluation_run": run.model_dump(mode="json")},
        plan_version=_plan_version(state),
        controller_cycle=_controller_cycle(state),
    )
    return await _record(
        state,
        runtime,
        producer_node="evaluate_report",
        specs=[spec],
    )


async def persist_post_controller_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    if _runtime_boundary(runtime) is None:
        return {}
    raw_decision = state.get("post_synthesis_controller_decision")
    if raw_decision is None:
        return {}
    decision = _model(raw_decision, PostSynthesisRoutingDecision)
    raw_evaluation = state.get("post_synthesis_evaluation_run")
    evaluation_digest = (
        _stable_digest(_model(raw_evaluation, PostSynthesisEvaluationRun))
        if raw_evaluation is not None
        else None
    )
    spec = ArtifactRecordSpec(
        artifact_kind=DeepResearchArtifactKind.controller_transition,
        logical_artifact_id="post-synthesis-controller",
        payload={
            "decision": decision.model_dump(mode="json"),
            "evaluation_digest": evaluation_digest,
        },
        plan_version=_plan_version(state),
        controller_cycle=_controller_cycle(state),
    )
    return await _record(
        state,
        runtime,
        producer_node="post_controller",
        specs=[spec],
    )


async def persist_terminal_node(
    state: DeepResearchState,
    runtime: Runtime[DeepResearchContext] | None = None,
) -> dict:
    boundary = _runtime_boundary(runtime)
    if boundary is None:
        return {}
    terminal_status = state.get("terminal_status")
    if terminal_status not in {"completed", "incomplete"}:
        raise ValueError("terminal persistence requires a final graph status")
    accepted = terminal_status == "completed" and state.get("report_accepted") is True
    if terminal_status == "completed" and not accepted:
        raise ValueError("completed terminal requires an accepted report")
    if terminal_status == "incomplete" and (
        state.get("report_accepted") is True or state.get("final_report") is not None
    ):
        raise ValueError("incomplete terminal cannot publish a report")

    report_version = state.get("report_version")
    candidate = state.get("candidate_report")
    candidate_digest = None
    if candidate is not None:
        candidate_digest = report_digest(_model(candidate, ResearchReport))
    evaluation_digest = None
    controller_digest = None
    if terminal_status == "completed":
        if (
            isinstance(report_version, bool)
            or not isinstance(report_version, int)
            or report_version < 1
            or candidate is None
            or state.get("final_report") is None
        ):
            raise ValueError("completed terminal requires a versioned final report")
        final_digest = report_digest(
            _model(state.get("final_report"), ResearchReport)
        )
        raw_evaluation = state.get("post_synthesis_evaluation_run")
        raw_decision = state.get("post_synthesis_controller_decision")
        if raw_evaluation is None or raw_decision is None:
            raise ValueError("completed terminal requires post-quality artifacts")
        evaluation = _model(raw_evaluation, PostSynthesisEvaluationRun)
        decision = _model(raw_decision, PostSynthesisRoutingDecision)
        if (
            final_digest != candidate_digest
            or evaluation.status != "completed"
            or evaluation.report_digest != candidate_digest
            or evaluation.report_version != report_version
            or decision.route != "accept"
            or decision.report_digest != candidate_digest
            or decision.report_version != report_version
        ):
            raise ValueError("completed terminal quality bindings are inconsistent")
        evaluation_digest = _stable_digest(evaluation)
        controller_digest = _stable_digest(decision)
    payload = {
        "terminal_status": terminal_status,
        "terminal_reason": state.get("terminal_reason"),
        "workflow_error_code": state.get("workflow_error_code"),
        "report_accepted": accepted,
        "report_version": (
            report_version
            if isinstance(report_version, int) and not isinstance(report_version, bool)
            else None
        ),
        "report_digest": candidate_digest,
        "post_evaluation_digest": evaluation_digest,
        "controller_decision_digest": controller_digest,
    }
    spec = ArtifactRecordSpec(
        artifact_kind=DeepResearchArtifactKind.terminal_decision,
        logical_artifact_id="terminal",
        payload=payload,
        plan_version=_plan_version(state),
        controller_cycle=_controller_cycle(state),
        expected_version_number=1,
        singleton=True,
    )
    context, recorder, checkpoint_id = boundary
    receipt = await recorder.record_terminal(
        run_id=context["run_id"],
        workspace_id=context["workspace_id"],
        guest_id=context["guest_id"],
        source_checkpoint_id=checkpoint_id,
        graph_version=context["graph_version"],
        producer_node="finalize_terminal",
        spec=spec,
        status=(
            WorkflowRunStatus.completed
            if terminal_status == "completed"
            else WorkflowRunStatus.incomplete
        ),
    )
    await _emit_receipts([receipt])
    return {}


__all__ = [
    "ArtifactPersistenceRuntimeError",
    "persist_initial_plan_node",
    "persist_post_controller_node",
    "persist_post_evaluation_node",
    "persist_pre_controller_node",
    "persist_pre_evaluation_node",
    "persist_repair_plan_node",
    "persist_revised_candidate_node",
    "persist_sub_reports_node",
    "persist_synthesis_candidate_node",
    "persist_terminal_node",
]
