from __future__ import annotations

import importlib.util
import uuid
from dataclasses import dataclass
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime

from app.config import Settings
from app.deep_research.context import (
    DEEP_RESEARCH_GRAPH_VERSION,
    DeepResearchContext,
)
from app.deep_research.runtime import (
    DEEP_RESEARCH_DURABILITY,
    DEEP_RESEARCH_RECURSION_LIMIT,
    GRAPH_VERSION_METADATA_KEY,
    GUEST_METADATA_KEY,
    WORKSPACE_METADATA_KEY,
    DeepResearchCheckpointAlreadyExists,
    DeepResearchCheckpointNotFound,
    DeepResearchCheckpointSetupRequired,
    DeepResearchContextMismatch,
    DeepResearchDurabilityRequired,
    DeepResearchGraphVersionMismatch,
    DeepResearchInvalidRunId,
    DeepResearchRuntime,
    DeepResearchRuntimeError,
    DeepResearchRuntimeNotInitialized,
)
from app.deep_research.models import SubQuestion


class _ServiceState(TypedDict, total=False):
    topic: str
    completed_nodes: list[str]
    terminal_status: str | None


def _context(
    run_id: str,
    *,
    secret: str = "sk-runtime-service-secret",
    workspace_id: str | None = None,
    guest_id: str = "guest-runtime-service",
) -> DeepResearchContext:
    return DeepResearchContext(
        run_id=run_id,
        workspace_id=workspace_id or str(uuid.uuid4()),
        guest_id=guest_id,
        api_key=secret,
        base_url="https://private-llm.invalid/v1",
        model="runtime-test-model",
        graph_version=DEEP_RESEARCH_GRAPH_VERSION,
    )


def _compiled_service_graph(
    saver: InMemorySaver,
    calls: list[tuple[str, str]],
    *,
    interrupt_after: list[str] | None = None,
):
    async def plan(
        state: _ServiceState,
        runtime: Runtime[DeepResearchContext],
    ) -> _ServiceState:
        calls.append(("plan", runtime.context["run_id"]))
        return {"completed_nodes": [*(state.get("completed_nodes") or []), "plan"]}

    async def execute(
        state: _ServiceState,
        runtime: Runtime[DeepResearchContext],
    ) -> _ServiceState:
        calls.append(("execute", runtime.context["run_id"]))
        return {
            "completed_nodes": [*(state.get("completed_nodes") or []), "execute"],
            "terminal_status": "completed",
        }

    builder = StateGraph(_ServiceState, context_schema=DeepResearchContext)
    builder.add_node("plan", plan)
    builder.add_node("execute", execute)
    builder.set_entry_point("plan")
    builder.add_edge("plan", "execute")
    builder.add_edge("execute", END)
    return builder.compile(
        checkpointer=saver,
        interrupt_after=interrupt_after,
    )


async def _collect_events(runtime: DeepResearchRuntime, state, *, run_id: str, context):
    return [
        event
        async for event in runtime.astream_events(
            state,
            run_id=run_id,
            context=context,
        )
    ]


@pytest.mark.asyncio
async def test_runtime_process_resume_uses_full_uuid_and_skips_completed_nodes() -> None:
    run_id = str(uuid.uuid4())
    context = _context(run_id)
    saver = InMemorySaver()
    calls: list[tuple[str, str]] = []
    first_process = DeepResearchRuntime(
        _compiled_service_graph(saver, calls, interrupt_after=["plan"]),
        saver,
    )

    await _collect_events(
        first_process,
        {"topic": "checkpoint topic", "completed_nodes": []},
        run_id=run_id,
        context=context,
    )
    interrupted = await first_process.aget_state(run_id)

    assert interrupted.values["completed_nodes"] == ["plan"]
    assert interrupted.next == ("execute",)
    assert calls == [("plan", run_id)]

    second_process = DeepResearchRuntime(
        _compiled_service_graph(saver, calls),
        saver,
    )
    await _collect_events(
        second_process,
        None,
        run_id=run_id,
        context=context,
    )
    completed = await second_process.aget_state(run_id)

    assert completed.values["completed_nodes"] == ["plan", "execute"]
    assert completed.next == ()
    assert calls == [("plan", run_id), ("execute", run_id)]
    assert uuid.UUID(run_id).version == 4


@pytest.mark.asyncio
async def test_runtime_rejects_resume_without_checkpoint() -> None:
    run_id = str(uuid.uuid4())
    saver = InMemorySaver()
    runtime = DeepResearchRuntime(_compiled_service_graph(saver, []), saver)

    with pytest.raises(DeepResearchCheckpointNotFound):
        await _collect_events(
            runtime,
            None,
            run_id=run_id,
            context=_context(run_id),
        )


@pytest.mark.asyncio
async def test_runtime_rejects_new_input_for_existing_checkpoint() -> None:
    run_id = str(uuid.uuid4())
    saver = InMemorySaver()
    runtime = DeepResearchRuntime(_compiled_service_graph(saver, []), saver)
    context = _context(run_id)

    await _collect_events(
        runtime,
        {"topic": "first input"},
        run_id=run_id,
        context=context,
    )

    with pytest.raises(DeepResearchCheckpointAlreadyExists):
        await _collect_events(
            runtime,
            {"topic": "must not replace checkpoint"},
            run_id=run_id,
            context=context,
        )


@pytest.mark.asyncio
async def test_runtime_rejects_context_and_thread_cross_binding() -> None:
    run_id = str(uuid.uuid4())
    other_run_id = str(uuid.uuid4())
    saver = InMemorySaver()
    runtime = DeepResearchRuntime(_compiled_service_graph(saver, []), saver)

    with pytest.raises(DeepResearchContextMismatch):
        await _collect_events(
            runtime,
            {"topic": "cross-bound input"},
            run_id=run_id,
            context=_context(other_run_id),
        )


@pytest.mark.asyncio
async def test_runtime_graph_invocation_receives_fixed_recursion_limit() -> None:
    run_id = str(uuid.uuid4())
    context = _context(run_id)
    observed_configs: list[dict] = []

    class RecordingGraph:
        async def aget_state(self, config):
            observed_configs.append(config)
            return type(
                "EmptySnapshot",
                (),
                {"created_at": None, "metadata": {}, "values": {}, "next": ()},
            )()

        async def astream_events(
            self,
            _state,
            *,
            version,
            config,
            context,
            durability,
        ):
            assert version == "v2"
            assert durability == DEEP_RESEARCH_DURABILITY == "sync"
            observed_configs.append(config)
            if False:
                yield context

    runtime = DeepResearchRuntime(RecordingGraph(), checkpointer=object())
    await _collect_events(
        runtime,
        {"topic": "bounded recursive graph"},
        run_id=run_id,
        context=context,
    )

    assert len(observed_configs) == 2
    for config in observed_configs:
        assert config["recursion_limit"] == DEEP_RESEARCH_RECURSION_LIMIT == 128
        assert config["configurable"]["thread_id"] == run_id
    assert observed_configs[0]["metadata"] == {
        GRAPH_VERSION_METADATA_KEY: DEEP_RESEARCH_GRAPH_VERSION
    }
    assert observed_configs[1]["metadata"] == {
        GRAPH_VERSION_METADATA_KEY: DEEP_RESEARCH_GRAPH_VERSION,
        WORKSPACE_METADATA_KEY: context["workspace_id"],
        GUEST_METADATA_KEY: context["guest_id"],
    }


@pytest.mark.asyncio
async def test_runtime_rejects_checkpoint_owner_mismatch_before_resume() -> None:
    run_id = str(uuid.uuid4())
    saver = InMemorySaver()
    calls: list[tuple[str, str]] = []
    runtime = DeepResearchRuntime(
        _compiled_service_graph(saver, calls, interrupt_after=["plan"]),
        saver,
    )
    owner = _context(run_id)
    await _collect_events(
        runtime,
        {"topic": "tenant-bound checkpoint"},
        run_id=run_id,
        context=owner,
    )
    before = await runtime.aget_state(run_id)

    wrong_owners = (
        {**owner, "workspace_id": str(uuid.uuid4())},
        {**owner, "guest_id": "guest-other"},
    )
    for wrong_owner in wrong_owners:
        with pytest.raises(DeepResearchContextMismatch):
            await _collect_events(
                runtime,
                None,
                run_id=run_id,
                context=wrong_owner,
            )

    after = await runtime.aget_state(run_id)
    assert calls == [("plan", run_id)]
    assert after.values == before.values
    assert after.next == before.next == ("execute",)
    assert after.metadata[WORKSPACE_METADATA_KEY] == owner["workspace_id"]
    assert after.metadata[GUEST_METADATA_KEY] == owner["guest_id"]


@pytest.mark.asyncio
async def test_runtime_graph_version_mismatch_fails_before_resume() -> None:
    run_id = str(uuid.uuid4())
    saver = InMemorySaver()
    initial = DeepResearchRuntime(
        _compiled_service_graph(saver, [], interrupt_after=["plan"]),
        saver,
    )
    await _collect_events(
        initial,
        {"topic": "versioned checkpoint"},
        run_id=run_id,
        context=_context(run_id),
    )

    incompatible = DeepResearchRuntime(
        _compiled_service_graph(saver, []),
        saver,
        graph_version="deep-research.v999",
    )

    with pytest.raises(DeepResearchGraphVersionMismatch):
        await incompatible.aget_state(run_id)
    with pytest.raises(DeepResearchGraphVersionMismatch):
        await _collect_events(
            incompatible,
            None,
            run_id=run_id,
            context={**_context(run_id), "graph_version": "deep-research.v999"},
        )


@pytest.mark.asyncio
async def test_resume_caller_cannot_overwrite_checkpoint_graph_version() -> None:
    run_id = str(uuid.uuid4())
    saver = InMemorySaver()
    runtime = DeepResearchRuntime(
        _compiled_service_graph(saver, [], interrupt_after=["plan"]),
        saver,
    )
    context = _context(run_id)
    await _collect_events(
        runtime,
        {
            "topic": "authoritative checkpoint version",
            GRAPH_VERSION_METADATA_KEY: "attacker-input-version",
        },
        run_id=run_id,
        context=context,
    )
    before = await runtime.aget_state(run_id)
    assert before.metadata[GRAPH_VERSION_METADATA_KEY] == DEEP_RESEARCH_GRAPH_VERSION

    with pytest.raises(DeepResearchContextMismatch):
        await _collect_events(
            runtime,
            None,
            run_id=run_id,
            context={**context, "graph_version": "attacker-context-version"},
        )
    with pytest.raises(DeepResearchCheckpointAlreadyExists):
        await _collect_events(
            runtime,
            {
                "topic": "attempted replacement input",
                GRAPH_VERSION_METADATA_KEY: "attacker-input-version",
            },
            run_id=run_id,
            context=context,
        )

    after = await runtime.aget_state(run_id)
    assert after.metadata[GRAPH_VERSION_METADATA_KEY] == DEEP_RESEARCH_GRAPH_VERSION
    assert after.values == before.values


@pytest.mark.asyncio
async def test_runtime_missing_checkpoint_version_metadata_fails_closed() -> None:
    run_id = str(uuid.uuid4())
    saver = InMemorySaver()
    raw_graph = _compiled_service_graph(saver, [])
    await raw_graph.ainvoke(
        {"topic": "legacy unversioned checkpoint"},
        config={"configurable": {"thread_id": run_id}},
        context=_context(run_id),
    )
    runtime = DeepResearchRuntime(raw_graph, saver)

    with pytest.raises(DeepResearchGraphVersionMismatch) as exc_info:
        await runtime.aget_state(run_id)

    assert exc_info.value.actual is None
    assert exc_info.value.expected == DEEP_RESEARCH_GRAPH_VERSION


@pytest.mark.asyncio
async def test_runtime_requires_canonical_full_uuid() -> None:
    saver = InMemorySaver()
    runtime = DeepResearchRuntime(_compiled_service_graph(saver, []), saver)

    for invalid in ("run-1", "96eff9be", str(uuid.uuid4()).upper()):
        with pytest.raises(DeepResearchInvalidRunId):
            await runtime.aget_state(invalid)


@pytest.mark.asyncio
async def test_runtime_terminal_resume_is_idempotent() -> None:
    run_id = str(uuid.uuid4())
    saver = InMemorySaver()
    calls: list[tuple[str, str]] = []
    runtime = DeepResearchRuntime(_compiled_service_graph(saver, calls), saver)
    context = _context(run_id)
    await _collect_events(
        runtime,
        {"topic": "terminal checkpoint"},
        run_id=run_id,
        context=context,
    )
    before = await runtime.aget_state(run_id)

    repeated_events = await _collect_events(
        runtime,
        None,
        run_id=run_id,
        context=context,
    )
    after = await runtime.aget_state(run_id)

    assert not any(
        event.get("event") == "on_chain_start"
        and event.get("name") in {"plan", "execute"}
        for event in repeated_events
    )
    assert after.values == before.values
    assert after.next == before.next == ()
    assert calls == [("plan", run_id), ("execute", run_id)]


@pytest.mark.asyncio
async def test_runtime_delete_thread_is_idempotent() -> None:
    run_id = str(uuid.uuid4())
    saver = InMemorySaver()
    runtime = DeepResearchRuntime(_compiled_service_graph(saver, []), saver)
    await _collect_events(
        runtime,
        {"topic": "deletable checkpoint"},
        run_id=run_id,
        context=_context(run_id),
    )

    await runtime.adelete_thread(run_id)
    await runtime.adelete_thread(run_id)

    with pytest.raises(DeepResearchCheckpointNotFound):
        await runtime.aget_state(run_id)


@pytest.mark.asyncio
async def test_two_similar_uuid_threads_never_cross_bind() -> None:
    prefix = uuid.uuid4().hex[:8]
    run_a = str(uuid.UUID(f"{prefix}{uuid.uuid4().hex[8:]}"))
    run_b = str(uuid.UUID(f"{prefix}{uuid.uuid4().hex[8:]}"))
    saver = InMemorySaver()
    runtime = DeepResearchRuntime(_compiled_service_graph(saver, []), saver)

    await _collect_events(
        runtime,
        {"topic": "thread A"},
        run_id=run_a,
        context=_context(run_a),
    )
    await _collect_events(
        runtime,
        {"topic": "thread B"},
        run_id=run_b,
        context=_context(run_b),
    )

    assert (await runtime.aget_state(run_a)).values["topic"] == "thread A"
    assert (await runtime.aget_state(run_b)).values["topic"] == "thread B"


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["test", "development"])
async def test_nonproduction_factory_uses_memory_with_strict_non_pickle_serializer(
    environment: str,
) -> None:
    runtime = await DeepResearchRuntime.create(Settings(environment=environment))
    try:
        assert runtime.backend == "memory"
        assert isinstance(runtime.checkpointer, InMemorySaver)
        serializer = runtime.checkpointer.serde
        assert isinstance(serializer, JsonPlusSerializer)
        assert serializer.pickle_fallback is False

        question = SubQuestion(
            id="sq-serializer",
            question="Can this approved model be reconstructed?",
            search_queries=["approved model roundtrip"],
            priority=1,
            rationale="Checkpoint serializer contract",
        )
        restored = serializer.loads_typed(serializer.dumps_typed(question))
        assert restored == question
        assert isinstance(restored, SubQuestion)
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_production_rejects_disabled_durable_checkpoints() -> None:
    with pytest.raises(DeepResearchDurabilityRequired):
        await DeepResearchRuntime.create(
            Settings(
                environment="production",
                deep_research_checkpoint_enabled=False,
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("aes_key", ["", "too-short", "x" * 17])
async def test_production_rejects_invalid_encryption_key_before_storage(
    aes_key: str,
) -> None:
    storage_was_built = False

    def graph_builder():
        nonlocal storage_was_built
        storage_was_built = True
        raise AssertionError("graph/storage construction must not be reached")

    with pytest.raises(DeepResearchRuntimeError, match="AES_KEY"):
        await DeepResearchRuntime.create(
            Settings(
                environment="production",
                deep_research_checkpoint_enabled=True,
                deep_research_checkpoint_aes_key=aes_key,
            ),
            graph_builder=graph_builder,
        )

    assert storage_was_built is False


@pytest.mark.asyncio
async def test_production_rejects_checkpoint_auto_setup_before_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encryption_was_read = False

    def fail_if_encryption_is_read(_settings: Settings) -> bytes:
        nonlocal encryption_was_read
        encryption_was_read = True
        raise AssertionError("durable storage initialization must not be reached")

    monkeypatch.setattr(
        "app.deep_research.runtime._encryption_key",
        fail_if_encryption_is_read,
    )

    with pytest.raises(DeepResearchCheckpointSetupRequired):
        await DeepResearchRuntime.create(
            Settings(
                environment="production",
                deep_research_checkpoint_enabled=True,
                deep_research_checkpoint_auto_setup=True,
                deep_research_checkpoint_aes_key="x" * 32,
            )
        )

    assert encryption_was_read is False


@pytest.mark.asyncio
async def test_strict_serializer_does_not_reconstruct_unlisted_custom_class() -> None:
    @dataclass
    class UnlistedCheckpointPayload:
        value: str

    runtime = await DeepResearchRuntime.create(Settings(environment="test"))
    try:
        serializer = runtime.checkpointer.serde
        permissive_writer = JsonPlusSerializer(
            pickle_fallback=False,
            allowed_msgpack_modules=[
                (
                    UnlistedCheckpointPayload.__module__,
                    UnlistedCheckpointPayload.__name__,
                )
            ],
        )
        encoded = permissive_writer.dumps_typed(
            UnlistedCheckpointPayload(value="must-not-construct")
        )

        restored = serializer.loads_typed(encoded)

        assert restored == {"value": "must-not-construct"}
        assert not isinstance(restored, UnlistedCheckpointPayload)
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_runtime_close_callback_is_idempotent_and_closes_execution() -> None:
    close_calls = 0

    async def close_callback() -> None:
        nonlocal close_calls
        close_calls += 1

    runtime = DeepResearchRuntime(
        compiled_graph=object(),
        checkpointer=object(),
        close_callback=close_callback,
    )
    await runtime.aclose()
    await runtime.aclose()

    assert close_calls == 1
    run_id = str(uuid.uuid4())
    with pytest.raises(DeepResearchRuntimeNotInitialized):
        await _collect_events(
            runtime,
            {"topic": "must not execute"},
            run_id=run_id,
            context=_context(run_id),
        )


@pytest.mark.skipif(
    importlib.util.find_spec("langgraph.checkpoint.postgres") is None,
    reason="langgraph-checkpoint-postgres is not installed in the dependency image",
)
def test_postgres_checkpointer_integration_is_explicitly_separate() -> None:
    # A live database round-trip belongs in the opt-in integration suite. This
    # guard prevents an in-memory unit test from being mistaken for durable,
    # multi-process PostgreSQL verification.
    assert importlib.util.find_spec("langgraph.checkpoint.postgres") is not None
