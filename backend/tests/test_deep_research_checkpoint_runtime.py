from __future__ import annotations

import json
import uuid
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime

from app.deep_research.context import (
    DEEP_RESEARCH_GRAPH_VERSION,
    DeepResearchContext,
)
from app.deep_research.state import DeepResearchState


_SECRET_STATE_KEYS = {
    "api_key",
    "authorization",
    "base_url",
    "client_secret",
    "llm_base_url",
    "llm_model",
    "password",
    "refresh_token",
}


class _ResumeState(TypedDict, total=False):
    completed_nodes: list[str]
    terminal_status: str | None
    graph_version: str


def _runtime_context(*, run_id: str, api_key: str) -> DeepResearchContext:
    return DeepResearchContext(
        run_id=run_id,
        workspace_id=str(uuid.uuid4()),
        guest_id="guest-checkpoint-test",
        api_key=api_key,
        base_url="https://llm.invalid/v1",
        model="test-model",
        graph_version=DEEP_RESEARCH_GRAPH_VERSION,
    )


def _checkpoint_graph(
    *,
    checkpointer: InMemorySaver,
    calls: list[tuple[str, str]],
    interrupt_after: list[str] | None = None,
):
    async def plan(
        state: _ResumeState,
        runtime: Runtime[DeepResearchContext],
    ) -> _ResumeState:
        calls.append(("plan", runtime.context["api_key"]))
        return {
            "completed_nodes": [*(state.get("completed_nodes") or []), "plan"],
            "graph_version": runtime.context["graph_version"],
        }

    async def execute(
        state: _ResumeState,
        runtime: Runtime[DeepResearchContext],
    ) -> _ResumeState:
        calls.append(("execute", runtime.context["api_key"]))
        return {
            "completed_nodes": [*(state.get("completed_nodes") or []), "execute"],
            "terminal_status": "completed",
        }

    builder = StateGraph(_ResumeState, context_schema=DeepResearchContext)
    builder.add_node("plan", plan)
    builder.add_node("execute", execute)
    builder.set_entry_point("plan")
    builder.add_edge("plan", "execute")
    builder.add_edge("execute", END)
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_after=interrupt_after,
    )


def _checkpoint_json(snapshot: object) -> str:
    def default(value: object) -> object:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "_asdict"):
            return value._asdict()
        return repr(value)

    return json.dumps(snapshot, default=default, sort_keys=True)


def test_checkpointed_state_schema_has_no_runtime_credentials() -> None:
    state_keys = set(DeepResearchState.__annotations__)

    assert state_keys.isdisjoint(_SECRET_STATE_KEYS)
    assert {
        "run_id",
        "workspace_id",
        "guest_id",
        "api_key",
        "base_url",
        "model",
        "graph_version",
    } <= set(DeepResearchContext.__annotations__)


@pytest.mark.asyncio
async def test_process_style_resume_reinjects_context_and_skips_completed_nodes() -> None:
    secret = "sk-runtime-only-checkpoint-secret"
    run_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": run_id}}
    context = _runtime_context(run_id=run_id, api_key=secret)
    saver = InMemorySaver()
    calls: list[tuple[str, str]] = []

    first_process = _checkpoint_graph(
        checkpointer=saver,
        calls=calls,
        interrupt_after=["plan"],
    )
    first_result = await first_process.ainvoke(
        {
            "completed_nodes": [],
            "terminal_status": None,
            "graph_version": DEEP_RESEARCH_GRAPH_VERSION,
        },
        config=config,
        context=context,
    )

    assert first_result["completed_nodes"] == ["plan"]
    assert calls == [("plan", secret)]

    first_snapshot = await first_process.aget_state(config)
    checkpoint_payload = _checkpoint_json(first_snapshot.values)
    assert secret not in checkpoint_payload
    assert "api_key" not in checkpoint_payload
    assert "base_url" not in checkpoint_payload

    # A new compiled graph represents a fresh process using the same durable
    # checkpoint backend. ``None`` means resume, not a new initial input.
    second_process = _checkpoint_graph(checkpointer=saver, calls=calls)
    final_result = await second_process.ainvoke(
        None,
        config=config,
        context=context,
    )

    assert final_result["completed_nodes"] == ["plan", "execute"]
    assert final_result["terminal_status"] == "completed"
    assert calls == [("plan", secret), ("execute", secret)]

    final_snapshot = await second_process.aget_state(config)
    assert final_snapshot.next == ()
    assert secret not in _checkpoint_json(final_snapshot.values)


@pytest.mark.asyncio
async def test_terminal_resume_is_idempotent() -> None:
    secret = "sk-terminal-resume-secret"
    run_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": run_id}}
    context = _runtime_context(run_id=run_id, api_key=secret)
    calls: list[tuple[str, str]] = []
    graph = _checkpoint_graph(checkpointer=InMemorySaver(), calls=calls)

    result = await graph.ainvoke(
        {
            "completed_nodes": [],
            "terminal_status": None,
            "graph_version": DEEP_RESEARCH_GRAPH_VERSION,
        },
        config=config,
        context=context,
    )
    repeated = await graph.ainvoke(None, config=config, context=context)

    assert repeated == result
    assert calls == [("plan", secret), ("execute", secret)]


@pytest.mark.asyncio
async def test_checkpoint_thread_id_is_the_full_run_uuid() -> None:
    run_id = str(uuid.uuid4())
    parsed = uuid.UUID(run_id)
    saver = InMemorySaver()
    graph = _checkpoint_graph(checkpointer=saver, calls=[])
    config = {"configurable": {"thread_id": run_id}}

    await graph.ainvoke(
        {
            "completed_nodes": [],
            "terminal_status": None,
            "graph_version": DEEP_RESEARCH_GRAPH_VERSION,
        },
        config=config,
        context=_runtime_context(run_id=run_id, api_key="test-key"),
    )
    tuple_ = await saver.aget_tuple(config)

    assert parsed.version == 4
    assert tuple_ is not None
    assert tuple_.config["configurable"]["thread_id"] == run_id
