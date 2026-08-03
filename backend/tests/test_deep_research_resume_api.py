from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
import uuid

import pytest
from fastapi import HTTPException

from app.api import deep_research as api
from app.deep_research.runtime import (
    DEEP_RESEARCH_GRAPH_VERSION,
    DeepResearchCheckpointNotFound,
    DeepResearchGraphVersionMismatch,
)
from app.deep_research.events import (
    DEEP_RESEARCH_EVENT_SCHEMA_VERSION,
    validate_run_event_payload,
)
from app.models.orm import WorkflowRunStatus
from app.models.schemas import DeepResearchResumeRequest


class _Lock:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def __aenter__(self):
        self.calls.append("lock_enter")
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        self.calls.append("lock_exit")
        return False


class _FakeEventWriter:
    """Exercise the v1 payload contract without an application database."""

    seq = 0

    def __init__(self, *, run_id, workspace_id, guest_id, initial_state=None):
        self.run_id = run_id
        self.workspace_id = workspace_id
        self.guest_id = guest_id
        self.state = dict(initial_state or {})
        self.checkpoint_id = None

    def update_state(self, update):
        if isinstance(update, dict):
            self.state.update(update)

    def event_id(self, event_type: str, boundary: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.run_id}:{event_type}:{boundary}"))

    async def append(self, event_type, payload, *, boundary, checkpoint_id=None):
        validate_run_event_payload(event_type, payload)
        type(self).seq += 1
        report_version = self.state.get("report_version")
        if not isinstance(report_version, int) or report_version < 1:
            report_version = None
        return SimpleNamespace(
            schema_version=DEEP_RESEARCH_EVENT_SCHEMA_VERSION,
            event_id=self.event_id(event_type, boundary),
            seq=type(self).seq,
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


def _patch_v1_event_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_events(**_kwargs):
        return []

    async def no_artifacts(**_kwargs):
        return []

    monkeypatch.setattr(api, "DurableRunEventWriter", _FakeEventWriter)
    monkeypatch.setattr(api, "_all_owned_run_events", no_events)
    monkeypatch.setattr(api, "_owned_artifacts", no_artifacts)


def _failed_run(run_id: str, workspace_id: str):
    return SimpleNamespace(
        id=run_id,
        workspace_id=workspace_id,
        status=WorkflowRunStatus.failed,
    )


@pytest.mark.asyncio
async def test_failed_resume_authorizes_owner_before_checkpoint_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    owned_lookups: list[dict[str, Any]] = []

    async def get_owned_workflow_run(**kwargs):
        owned_lookups.append(kwargs)
        return None

    def checkpoint_must_not_be_touched():
        raise AssertionError("checkpoint access must follow ownership authorization")

    monkeypatch.setattr(api, "get_owned_workflow_run", get_owned_workflow_run)
    monkeypatch.setattr(
        api,
        "get_deep_research_runtime",
        checkpoint_must_not_be_touched,
    )

    with pytest.raises(HTTPException) as exc_info:
        await api.resume_deep_research_stream(
            run_id,
            DeepResearchResumeRequest(workspace_id=workspace_id),
            guest_id="guest-not-owner",
        )

    assert exc_info.value.status_code == 404
    assert owned_lookups == [
        {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "guest_id": "guest-not-owner",
            "run_type": api.WorkflowRunType.deep_research,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("checkpoint_outcome", "expected_code"),
    [
        ("missing", "checkpoint_not_found"),
        ("wrong_version", "graph_version_mismatch"),
        ("no_next", "checkpoint_not_restorable"),
    ],
)
async def test_failed_resume_rejects_nonrestorable_checkpoint_before_status_change(
    monkeypatch: pytest.MonkeyPatch,
    checkpoint_outcome: str,
    expected_code: str,
) -> None:
    run_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    run = _failed_run(run_id, workspace_id)
    calls: list[str] = []

    async def require_owned(**kwargs):
        assert kwargs == {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "guest_id": "guest-owner",
        }
        calls.append("owned")
        return run

    class Runtime:
        async def aget_state(self, actual_run_id: str):
            assert actual_run_id == run_id
            calls.append("checkpoint")
            if checkpoint_outcome == "missing":
                raise DeepResearchCheckpointNotFound("missing")
            if checkpoint_outcome == "wrong_version":
                raise DeepResearchGraphVersionMismatch(
                    expected=DEEP_RESEARCH_GRAPH_VERSION,
                    actual="deep-research.v0",
                )
            return SimpleNamespace(next=())

    async def mark_running(**_kwargs):
        raise AssertionError("a non-restorable failed run must remain failed")

    monkeypatch.setattr(api, "_require_owned_deep_research_run", require_owned)
    monkeypatch.setattr(api, "get_deep_research_runtime", lambda: Runtime())
    monkeypatch.setattr(api, "deep_research_run_lock", lambda _run_id: _Lock(calls))
    monkeypatch.setattr(api, "mark_workflow_run_running", mark_running)

    with pytest.raises(HTTPException) as exc_info:
        await api.resume_deep_research_stream(
            run_id,
            DeepResearchResumeRequest(workspace_id=workspace_id),
            guest_id="guest-owner",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == expected_code
    assert calls == ["owned", "lock_enter", "owned", "checkpoint", "lock_exit"]
    assert run.status == WorkflowRunStatus.failed


@pytest.mark.asyncio
async def test_failed_resume_uses_null_input_only_after_restorable_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    run = _failed_run(run_id, workspace_id)
    calls: list[str] = []
    streamed_inputs: list[Any] = []
    secret = "sk-resume-runtime-only"

    async def require_owned(**kwargs):
        assert kwargs == {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "guest_id": "guest-owner",
        }
        calls.append("owned")
        return run

    class Runtime:
        def __init__(self) -> None:
            self.state_reads = 0

        async def aget_state(self, actual_run_id: str):
            assert actual_run_id == run_id
            self.state_reads += 1
            calls.append(f"checkpoint_{self.state_reads}")
            if self.state_reads == 1:
                return SimpleNamespace(
                    next=("execute",),
                    values={"topic": "Resume fixture"},
                    config={"configurable": {"checkpoint_id": "checkpoint-1"}},
                )
            return SimpleNamespace(
                next=(),
                values={
                    "terminal_status": "incomplete",
                    "terminal_reason": "Quality gate stopped the run.",
                    "report_accepted": False,
                },
            )

        async def astream_events(
            self,
            input_state,
            actual_run_id: str,
            context,
            callbacks=None,
        ):
            assert actual_run_id == run_id
            assert context["api_key"] == secret
            assert callbacks is None
            streamed_inputs.append(input_state)
            calls.append("stream")
            if False:
                yield {}

    runtime = Runtime()

    async def resolve_llm(_guest_id: str):
        return SimpleNamespace(
            resolved=SimpleNamespace(
                api_key=secret,
                base_url="https://private.invalid/v1",
                model="resume-test-model",
            )
        )

    def build_context(**kwargs):
        assert kwargs["run_id"] == run_id
        assert kwargs["workspace_id"] == workspace_id
        assert kwargs["guest_id"] == "guest-owner"
        return {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "guest_id": "guest-owner",
            "api_key": secret,
            "graph_version": DEEP_RESEARCH_GRAPH_VERSION,
        }

    async def mark_running(**kwargs):
        assert kwargs == {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "guest_id": "guest-owner",
        }
        calls.append("mark_running")
        run.status = WorkflowRunStatus.running
        return run

    async def verify_terminal(**kwargs):
        assert kwargs["run_id"] == run_id
        assert kwargs["workspace_id"] == workspace_id
        assert kwargs["guest_id"] == "guest-owner"
        assert kwargs["state"]["terminal_status"] == "incomplete"
        calls.append("verify_terminal")
        return run

    monkeypatch.setattr(api, "_require_owned_deep_research_run", require_owned)
    monkeypatch.setattr(api, "get_deep_research_runtime", lambda: runtime)
    monkeypatch.setattr(api, "deep_research_run_lock", lambda _run_id: _Lock(calls))
    monkeypatch.setattr(api, "_resolve_llm", resolve_llm)
    monkeypatch.setattr(api, "_build_graph_context", build_context)
    monkeypatch.setattr(api, "mark_workflow_run_running", mark_running)
    monkeypatch.setattr(api, "_verify_graph_terminal_persisted", verify_terminal)
    monkeypatch.setattr(api, "create_trace", lambda **_kwargs: None)
    monkeypatch.setattr(api, "get_langfuse_callback_handler", lambda _trace: None)
    _patch_v1_event_seams(monkeypatch)

    response = await api.resume_deep_research_stream(
        run_id,
        DeepResearchResumeRequest(workspace_id=workspace_id),
        guest_id="guest-owner",
    )
    chunks = [chunk async for chunk in response.body_iterator]
    rendered = "".join(
        chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk for chunk in chunks
    )

    assert streamed_inputs == [None]
    assert '"status":"incomplete"' in rendered
    assert secret not in rendered
    assert calls == [
        "owned",
        "lock_enter",
        "owned",
        "checkpoint_1",
        "mark_running",
        "stream",
        "checkpoint_2",
        "verify_terminal",
        "lock_exit",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "result_status"),
    [
        (WorkflowRunStatus.completed, "completed"),
        (WorkflowRunStatus.incomplete, "incomplete"),
    ],
)
async def test_terminal_resume_replays_without_runtime_or_llm(
    monkeypatch: pytest.MonkeyPatch,
    status: WorkflowRunStatus,
    result_status: str,
) -> None:
    run_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    run = SimpleNamespace(id=run_id, workspace_id=workspace_id, status=status)
    calls: list[str] = []

    async def require_owned(**_kwargs):
        calls.append("owned")
        return run

    async def persisted_terminal(actual_run, *, guest_id: str):
        assert actual_run is run
        assert guest_id == "guest-owner"
        calls.append("terminal_replay")
        return api.DeepResearchRunResult(
            run_id=run_id,
            status=result_status,
            message="Persisted terminal outcome",
        )

    def must_not_run():
        raise AssertionError("terminal replay must not initialize the runtime")

    async def llm_must_not_run(_guest_id: str):
        raise AssertionError("terminal replay must not resolve the LLM")

    monkeypatch.setattr(api, "_require_owned_deep_research_run", require_owned)
    monkeypatch.setattr(api, "_persisted_terminal_result", persisted_terminal)
    monkeypatch.setattr(api, "get_deep_research_runtime", must_not_run)
    monkeypatch.setattr(api, "_resolve_llm", llm_must_not_run)
    _patch_v1_event_seams(monkeypatch)

    response = await api.resume_deep_research_stream(
        run_id,
        DeepResearchResumeRequest(workspace_id=workspace_id),
        guest_id="guest-owner",
    )
    chunks = [chunk async for chunk in response.body_iterator]
    rendered = "".join(
        chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        for chunk in chunks
    )

    assert f'"status":"{result_status}"' in rendered
    assert calls == ["owned", "terminal_replay"]


@pytest.mark.asyncio
async def test_resume_cancellation_marks_interrupted_and_releases_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    run = _failed_run(run_id, workspace_id)
    calls: list[str] = []

    async def require_owned(**_kwargs):
        calls.append("owned")
        return run

    class Runtime:
        async def aget_state(self, _run_id: str):
            calls.append("checkpoint")
            return SimpleNamespace(
                next=("execute",),
                values={"topic": "Cancellation fixture"},
                config={"configurable": {"checkpoint_id": "checkpoint-1"}},
            )

        async def astream_events(self, _input, _run_id, _context, callbacks=None):
            del callbacks
            calls.append("stream_cancel")
            raise asyncio.CancelledError
            if False:
                yield {}

    async def resolve_llm(_guest_id: str):
        return SimpleNamespace(
            resolved=SimpleNamespace(api_key="test", base_url=None, model="test")
        )

    async def mark_running(**_kwargs):
        calls.append("mark_running")
        run.status = WorkflowRunStatus.running
        return run

    async def record_stop(**kwargs):
        assert kwargs["status"] == WorkflowRunStatus.interrupted
        assert kwargs["code"] == "execution_interrupted"
        calls.append("mark_interrupted")

    async def no_committed_terminal(**_kwargs):
        return None

    monkeypatch.setattr(api, "_require_owned_deep_research_run", require_owned)
    monkeypatch.setattr(api, "get_deep_research_runtime", lambda: Runtime())
    monkeypatch.setattr(api, "deep_research_run_lock", lambda _run_id: _Lock(calls))
    monkeypatch.setattr(api, "_resolve_llm", resolve_llm)
    monkeypatch.setattr(api, "mark_workflow_run_running", mark_running)
    monkeypatch.setattr(api, "_record_execution_stop", record_stop)
    monkeypatch.setattr(
        api,
        "_committed_terminal_result_or_none",
        no_committed_terminal,
    )
    monkeypatch.setattr(api, "create_trace", lambda **_kwargs: None)
    monkeypatch.setattr(api, "get_langfuse_callback_handler", lambda _trace: None)
    _patch_v1_event_seams(monkeypatch)

    response = await api.resume_deep_research_stream(
        run_id,
        DeepResearchResumeRequest(workspace_id=workspace_id),
        guest_id="guest-owner",
    )
    with pytest.raises(asyncio.CancelledError):
        _ = [chunk async for chunk in response.body_iterator]

    assert calls == [
        "owned",
        "lock_enter",
        "owned",
        "checkpoint",
        "mark_running",
        "stream_cancel",
        "checkpoint",
        "mark_interrupted",
        "lock_exit",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "allowed", "reason_code", "checkpoint_available"),
    [
        ("restorable", True, "checkpoint_restorable", True),
        ("no_next", False, "checkpoint_not_restorable", True),
        ("wrong_version", False, "graph_version_mismatch", False),
    ],
)
async def test_get_run_exposes_truthful_checkpoint_capability(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    allowed: bool,
    reason_code: str,
    checkpoint_available: bool,
) -> None:
    run_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    now = datetime.utcnow()
    run = SimpleNamespace(
        id=run_id,
        workspace_id=workspace_id,
        status=WorkflowRunStatus.failed,
        current_stage="executing",
        stages_completed=["planning", "executing"],
        created_at=now,
        updated_at=now,
        completed_at=now,
        input_payload={"input": {"topic": "Checkpoint fixture"}},
    )

    async def require_owned(**_kwargs):
        return run

    class Runtime:
        backend = "postgres"

        async def aget_state(self, _run_id: str):
            if outcome == "wrong_version":
                raise DeepResearchGraphVersionMismatch(
                    expected=DEEP_RESEARCH_GRAPH_VERSION,
                    actual="deep-research.v0",
                )
            return SimpleNamespace(
                next=("execute",) if outcome == "restorable" else (),
                config={"configurable": {"checkpoint_id": "checkpoint-1"}},
            )

    monkeypatch.setattr(api, "_require_owned_deep_research_run", require_owned)
    monkeypatch.setattr(api, "get_deep_research_runtime", lambda: Runtime())
    _patch_v1_event_seams(monkeypatch)

    result = await api.get_deep_research_run(
        run_id,
        workspace_id=workspace_id,
        guest_id="guest-owner",
    )

    assert result["schema_version"] == "deep-research-run.v1"
    assert result["status"] == "failed"
    assert result["resume"]["allowed"] is allowed
    assert result["resume"]["reason_code"] == reason_code
    assert (result["resume"]["checkpoint_id"] is not None) is checkpoint_available


@pytest.mark.asyncio
async def test_artifact_read_authorizes_then_honors_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    calls: list[tuple[str, Any]] = []

    async def require_owned(**kwargs):
        calls.append(("owned", kwargs))
        return _failed_run(run_id, workspace_id)

    async def owned_artifacts(**kwargs):
        calls.append(("artifacts", kwargs))
        return []

    monkeypatch.setattr(api, "_require_owned_deep_research_run", require_owned)
    monkeypatch.setattr(api, "_owned_artifacts", owned_artifacts)

    result = await api.list_deep_research_run_artifacts(
        run_id,
        workspace_id=workspace_id,
        snapshot=True,
        guest_id="guest-owner",
    )

    assert result == []
    assert calls == [
        (
            "owned",
            {
                "run_id": run_id,
                "workspace_id": workspace_id,
                "guest_id": "guest-owner",
            },
        ),
        (
            "artifacts",
            {
                "run_id": run_id,
                "workspace_id": workspace_id,
                "guest_id": "guest-owner",
                "snapshot": True,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_event_page_is_owned_ordered_and_cursor_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    calls: list[tuple[str, Any]] = []

    async def require_owned(**kwargs):
        calls.append(("owned", kwargs))
        return _failed_run(run_id, workspace_id)

    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return False

    class SessionFactory:
        def __call__(self):
            return SessionContext()

    def event(seq: int):
        return SimpleNamespace(
            schema_version=DEEP_RESEARCH_EVENT_SCHEMA_VERSION,
            event_id=str(uuid.uuid4()),
            seq=seq,
            type="protocol_error",
            run_id=run_id,
            emitted_at=datetime.now(timezone.utc),
            cycle=0,
            plan_version=1,
            corpus_version=0,
            report_version=None,
            checkpoint_id=None,
            payload={
                "code": "fixture",
                "message": "Fixture event.",
                "recoverable": True,
                "last_good_seq": seq - 1,
            },
        )

    async def list_events(_db, **kwargs):
        calls.append(("events", kwargs))
        return [event(6), event(7), event(8)]

    monkeypatch.setattr(api, "_require_owned_deep_research_run", require_owned)
    monkeypatch.setattr(api, "AsyncSessionLocal", SessionFactory())
    monkeypatch.setattr(api, "list_run_events", list_events)

    result = await api.list_deep_research_run_event_page(
        run_id,
        workspace_id=workspace_id,
        after_seq=5,
        limit=2,
        guest_id="guest-owner",
    )

    assert [item["seq"] for item in result["events"]] == [6, 7]
    assert result["next_after_seq"] == 7
    assert result["has_more"] is True
    assert calls[1] == (
        "events",
        {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "guest_id": "guest-owner",
            "after_seq": 5,
            "limit": 3,
        },
    )
