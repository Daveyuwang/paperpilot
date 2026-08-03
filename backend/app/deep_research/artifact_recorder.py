"""Runtime-only bridge from checkpointed graph boundaries to artifact rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.postgres import AsyncSessionLocal
from app.deep_research.artifacts import (
    ArtifactAppendReceipt,
    ArtifactAppendSpec,
    DeepResearchArtifactConflictError,
    append_artifact_batch,
    get_artifact_snapshot,
)
from app.deep_research.models import (
    PostSynthesisEvaluationRun,
    PostSynthesisRoutingDecision,
    RepairStage,
    ResearchReport,
    SubQuestion,
    SubReport,
)
from app.deep_research.nodes.post_controller import (
    _POST_SCORE_WEIGHTS,
    post_synthesis_evaluation_is_acceptable,
)
from app.deep_research.provenance import (
    evaluation_digest,
    report_digest,
)
from app.models.orm import (
    DeepResearchArtifactKind,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowRunType,
)


@dataclass(frozen=True, slots=True)
class ArtifactRecordSpec:
    artifact_kind: DeepResearchArtifactKind | str
    logical_artifact_id: str
    payload: Mapping[str, Any]
    plan_version: int = 0
    controller_cycle: int = 0
    schema_version: int = 1
    skip_if_unchanged: bool = False
    expected_version_number: int | None = None
    singleton: bool = False


class ArtifactRecorder(Protocol):
    async def record_batch(
        self,
        *,
        run_id: str,
        workspace_id: str,
        guest_id: str,
        source_checkpoint_id: str,
        graph_version: str,
        producer_node: str,
        specs: Sequence[ArtifactRecordSpec],
    ) -> list[ArtifactAppendReceipt]: ...

    async def record_terminal(
        self,
        *,
        run_id: str,
        workspace_id: str,
        guest_id: str,
        source_checkpoint_id: str,
        graph_version: str,
        producer_node: str,
        spec: ArtifactRecordSpec,
        status: WorkflowRunStatus,
    ) -> ArtifactAppendReceipt: ...


def _checkpoint_write_key(
    *,
    graph_version: str,
    producer_node: str,
    source_checkpoint_id: str,
    artifact_kind: DeepResearchArtifactKind | str,
    logical_artifact_id: str,
) -> str:
    """Bind idempotency to one stable producer-output checkpoint."""

    kind = DeepResearchArtifactKind(artifact_kind)
    identity = "\0".join(
        (
            graph_version,
            producer_node,
            source_checkpoint_id,
            kind.value,
            logical_artifact_id,
        )
    )
    return "checkpoint:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _append_spec(
    spec: ArtifactRecordSpec,
    *,
    graph_version: str,
    producer_node: str,
    source_checkpoint_id: str,
) -> ArtifactAppendSpec:
    return ArtifactAppendSpec(
        artifact_kind=spec.artifact_kind,
        logical_artifact_id=spec.logical_artifact_id,
        write_key=_checkpoint_write_key(
            graph_version=graph_version,
            producer_node=producer_node,
            source_checkpoint_id=source_checkpoint_id,
            artifact_kind=spec.artifact_kind,
            logical_artifact_id=spec.logical_artifact_id,
        ),
        payload=spec.payload,
        plan_version=spec.plan_version,
        controller_cycle=spec.controller_cycle,
        schema_version=spec.schema_version,
        source_checkpoint_id=source_checkpoint_id,
        skip_if_unchanged=spec.skip_if_unchanged,
        expected_version_number=spec.expected_version_number,
        singleton=spec.singleton,
    )


def _artifact_reference(artifact) -> dict[str, Any]:
    return {
        "artifact_id": artifact.id,
        "content_hash": artifact.content_hash,
        "version_number": artifact.version_number,
    }


def _model_digest(model) -> str:
    payload = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DeepResearchArtifactConflictError(
            f"{name} artifact payload is malformed"
        )
    return value


def _post_evaluation_is_acceptable(
    report: ResearchReport,
    run: PostSynthesisEvaluationRun,
    *,
    active_questions: list[SubQuestion],
    active_reports: list[SubReport],
) -> tuple[bool, float]:
    """Reuse the controller's authoritative acceptance predicate."""

    evaluation = run.evaluation
    if run.status != "completed" or evaluation is None:
        return False, 0.0
    scores = evaluation.scores
    weighted_score = round(
        sum(
            getattr(scores, name) * weight
            for name, weight in _POST_SCORE_WEIGHTS.items()
        ),
        2,
    )
    state = {
        "candidate_report": report,
        "report_version": run.report_version,
        "post_synthesis_evaluation_run": run,
        "sub_questions": active_questions,
        "sub_reports": active_reports,
    }
    return (
        post_synthesis_evaluation_is_acceptable(state),
        weighted_score,
    )


def _active_research_inputs(
    latest: Mapping[tuple[DeepResearchArtifactKind, str], Any],
) -> tuple[list[SubQuestion], list[SubReport]]:
    plan_artifact = latest.get((DeepResearchArtifactKind.plan, "active-plan"))
    if plan_artifact is None:
        raise DeepResearchArtifactConflictError(
            "completed terminal is missing the active research plan"
        )
    plan_payload = _require_mapping(plan_artifact.payload, name="active plan")
    raw_questions = plan_payload.get("sub_questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise DeepResearchArtifactConflictError(
            "active plan artifact has no sub-questions"
        )
    try:
        questions = [SubQuestion.model_validate(item) for item in raw_questions]
    except Exception as exc:
        raise DeepResearchArtifactConflictError(
            "active plan artifact is malformed"
        ) from exc
    question_ids = [question.id for question in questions]
    if len(question_ids) != len(set(question_ids)):
        raise DeepResearchArtifactConflictError(
            "active plan artifact contains duplicate sub-question IDs"
        )

    reports: list[SubReport] = []
    for question in questions:
        artifact = latest.get(
            (
                DeepResearchArtifactKind.sub_report,
                f"sub-question:{question.id}",
            )
        )
        if artifact is None:
            raise DeepResearchArtifactConflictError(
                "completed terminal is missing an active sub-report"
            )
        payload = _require_mapping(artifact.payload, name="active sub-report")
        try:
            report = SubReport.model_validate(payload.get("sub_report"))
        except Exception as exc:
            raise DeepResearchArtifactConflictError(
                "active sub-report artifact is malformed"
            ) from exc
        if report.sub_question_id != question.id:
            raise DeepResearchArtifactConflictError(
                "active sub-report artifact is bound to another question"
            )
        reports.append(report)
    return questions, reports


def _validate_completed_terminal_lineage(
    *,
    terminal_payload: Mapping[str, Any],
    candidate_artifact,
    evaluation_artifact,
    controller_artifact,
    active_questions: list[SubQuestion],
    active_reports: list[SubReport],
) -> None:
    """Revalidate the exact accepted report chain inside the terminal txn."""

    try:
        candidate_payload = _require_mapping(
            candidate_artifact.payload,
            name="candidate",
        )
        candidate_report = ResearchReport.model_validate(
            candidate_payload.get("candidate_report")
        )
        candidate_digest = report_digest(candidate_report)
        candidate_version = candidate_payload.get("report_version")
        if (
            isinstance(candidate_version, bool)
            or not isinstance(candidate_version, int)
            or candidate_version < 1
            or candidate_payload.get("report_accepted") is not False
            or candidate_payload.get("report_digest") != candidate_digest
            or candidate_artifact.version_number != candidate_version
        ):
            raise ValueError("candidate binding mismatch")

        evaluation_payload = _require_mapping(
            evaluation_artifact.payload,
            name="post evaluation",
        )
        evaluation_run = PostSynthesisEvaluationRun.model_validate(
            evaluation_payload.get("evaluation_run")
        )
        exact_evaluation_digest = evaluation_digest(evaluation_run)
        if (
            evaluation_run.status != "completed"
            or evaluation_run.evaluation is None
            or evaluation_run.report_digest != candidate_digest
            or evaluation_run.report_version != candidate_version
        ):
            raise ValueError("post evaluation binding mismatch")
        acceptable, weighted_score = _post_evaluation_is_acceptable(
            candidate_report,
            evaluation_run,
            active_questions=active_questions,
            active_reports=active_reports,
        )
        if not acceptable:
            raise ValueError("post evaluation does not satisfy acceptance gates")

        controller_payload = _require_mapping(
            controller_artifact.payload,
            name="post controller",
        )
        decision = PostSynthesisRoutingDecision.model_validate(
            controller_payload.get("decision")
        )
        if (
            decision.route != "accept"
            or decision.repair_stage != RepairStage.INITIAL
            or decision.reason_code != "post_quality_gate_passed"
            or decision.report_digest != candidate_digest
            or decision.report_version != candidate_version
            or decision.evaluation_digest != exact_evaluation_digest
            or decision.weighted_overall_score != weighted_score
            or decision.major_issue_ids
            or controller_payload.get("evaluation_digest")
            != exact_evaluation_digest
        ):
            raise ValueError("post controller binding mismatch")

        if (
            terminal_payload.get("terminal_status") != "completed"
            or terminal_payload.get("report_accepted") is not True
            or terminal_payload.get("report_digest") != candidate_digest
            or terminal_payload.get("report_version") != candidate_version
            or terminal_payload.get("post_evaluation_digest")
            != exact_evaluation_digest
            or terminal_payload.get("controller_decision_digest")
            != _model_digest(decision)
        ):
            raise ValueError("terminal subject binding mismatch")
    except DeepResearchArtifactConflictError:
        raise
    except Exception as exc:
        raise DeepResearchArtifactConflictError(
            "completed terminal artifact lineage is inconsistent"
        ) from exc


class PostgresArtifactRecorder:
    """Record a graph boundary in one application-database transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    ) -> None:
        self._session_factory = session_factory

    async def record_batch(
        self,
        *,
        run_id: str,
        workspace_id: str,
        guest_id: str,
        source_checkpoint_id: str,
        graph_version: str,
        producer_node: str,
        specs: Sequence[ArtifactRecordSpec],
    ) -> list[ArtifactAppendReceipt]:
        append_specs = [
            _append_spec(
                spec,
                graph_version=graph_version,
                producer_node=producer_node,
                source_checkpoint_id=source_checkpoint_id,
            )
            for spec in specs
        ]
        async with self._session_factory() as db:
            async with db.begin():
                return await append_artifact_batch(
                    db,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    guest_id=guest_id,
                    specs=append_specs,
                )

    async def record_terminal(
        self,
        *,
        run_id: str,
        workspace_id: str,
        guest_id: str,
        source_checkpoint_id: str,
        graph_version: str,
        producer_node: str,
        spec: ArtifactRecordSpec,
        status: WorkflowRunStatus,
    ) -> ArtifactAppendReceipt:
        if status not in {
            WorkflowRunStatus.completed,
            WorkflowRunStatus.incomplete,
        }:
            raise ValueError("terminal artifact requires completed or incomplete status")
        if not spec.singleton or spec.expected_version_number != 1:
            raise ValueError("terminal artifact must be the singleton logical version")

        async with self._session_factory() as db:
            async with db.begin():
                run = await db.scalar(
                    select(WorkflowRun)
                    .where(
                        WorkflowRun.id == run_id,
                        WorkflowRun.workspace_id == workspace_id,
                        WorkflowRun.guest_id == guest_id,
                        WorkflowRun.run_type == WorkflowRunType.deep_research,
                    )
                    .with_for_update()
                )
                if run is None:
                    # append_artifact_batch will expose the canonical ownership
                    # error; this keeps recorder error mapping in one place.
                    append_specs = [
                        _append_spec(
                            spec,
                            graph_version=graph_version,
                            producer_node=producer_node,
                            source_checkpoint_id=source_checkpoint_id,
                        )
                    ]
                    await append_artifact_batch(
                        db,
                        run_id=run_id,
                        workspace_id=workspace_id,
                        guest_id=guest_id,
                        specs=append_specs,
                    )
                    raise AssertionError("owned run check unexpectedly returned")

                if run.status in {
                    WorkflowRunStatus.completed,
                    WorkflowRunStatus.incomplete,
                } and run.status != status:
                    raise DeepResearchArtifactConflictError(
                        "workflow run already has a different terminal status"
                    )
                if run.status not in {
                    WorkflowRunStatus.running,
                    WorkflowRunStatus.interrupted,
                    status,
                }:
                    raise DeepResearchArtifactConflictError(
                        "workflow run cannot transition from its current status"
                    )

                snapshot = await get_artifact_snapshot(
                    db,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    guest_id=guest_id,
                )
                latest = {
                    (
                        DeepResearchArtifactKind(item.artifact_kind),
                        item.logical_artifact_id,
                    ): item
                    for item in snapshot
                }
                refs: dict[str, dict[str, Any]] = {}
                reference_keys = (
                    (
                        "report_candidate",
                        DeepResearchArtifactKind.report_candidate,
                        "candidate-report",
                    ),
                    (
                        "post_synthesis_evaluation",
                        DeepResearchArtifactKind.post_synthesis_evaluation,
                        "post-synthesis-evaluation",
                    ),
                    (
                        "post_synthesis_controller",
                        DeepResearchArtifactKind.controller_transition,
                        "post-synthesis-controller",
                    ),
                )
                for label, kind, logical_id in reference_keys:
                    artifact = latest.get((kind, logical_id))
                    if artifact is not None:
                        refs[label] = _artifact_reference(artifact)

                terminal_payload = dict(spec.payload)
                if (
                    terminal_payload.get("terminal_status") != status.value
                    or terminal_payload.get("report_accepted")
                    is not (status == WorkflowRunStatus.completed)
                ):
                    raise DeepResearchArtifactConflictError(
                        "terminal payload does not match the requested status"
                    )
                if status == WorkflowRunStatus.completed:
                    required_refs = {
                        "report_candidate",
                        "post_synthesis_evaluation",
                        "post_synthesis_controller",
                    }
                    if set(refs) != required_refs:
                        raise DeepResearchArtifactConflictError(
                            "completed terminal is missing accepted artifact lineage"
                        )
                    active_questions, active_reports = _active_research_inputs(latest)
                    _validate_completed_terminal_lineage(
                        terminal_payload=terminal_payload,
                        candidate_artifact=latest[
                            (
                                DeepResearchArtifactKind.report_candidate,
                                "candidate-report",
                            )
                        ],
                        evaluation_artifact=latest[
                            (
                                DeepResearchArtifactKind.post_synthesis_evaluation,
                                "post-synthesis-evaluation",
                            )
                        ],
                        controller_artifact=latest[
                            (
                                DeepResearchArtifactKind.controller_transition,
                                "post-synthesis-controller",
                            )
                        ],
                        active_questions=active_questions,
                        active_reports=active_reports,
                    )
                terminal_payload["artifact_refs"] = refs
                terminal_spec = replace(spec, payload=terminal_payload)
                receipts = await append_artifact_batch(
                    db,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    guest_id=guest_id,
                    specs=[
                        _append_spec(
                            terminal_spec,
                            graph_version=graph_version,
                            producer_node=producer_node,
                            source_checkpoint_id=source_checkpoint_id,
                        )
                    ],
                )
                receipt = receipts[0]

                run.status = status
                run.current_stage = status.value
                if run.completed_at is None:
                    run.completed_at = datetime.utcnow()
                run.updated_at = datetime.utcnow()
                public_artifacts = dict(run.artifacts or {})
                public_artifacts.update(
                    {
                        "terminal_status": status.value,
                        "report_accepted": status == WorkflowRunStatus.completed,
                        "terminal_artifact_id": receipt.artifact.id,
                        "terminal_artifact_hash": receipt.artifact.content_hash,
                    }
                )
                if status == WorkflowRunStatus.completed:
                    public_artifacts["report_artifact_id"] = refs[
                        "report_candidate"
                    ]["artifact_id"]
                    public_artifacts["report_artifact_hash"] = refs[
                        "report_candidate"
                    ]["content_hash"]
                else:
                    public_artifacts.pop("report_artifact_id", None)
                    public_artifacts.pop("report_artifact_hash", None)
                run.artifacts = public_artifacts
                await db.flush()
                return receipt


__all__ = [
    "ArtifactRecordSpec",
    "ArtifactRecorder",
    "PostgresArtifactRecorder",
]
