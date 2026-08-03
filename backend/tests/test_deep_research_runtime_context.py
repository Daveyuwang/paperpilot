"""Credential-isolation contracts for the Deep Research LangGraph runtime.

The graph may accept LLM credentials at invocation time, but credentials are
runtime context rather than research state.  This distinction matters once a
checkpointer is attached: graph state is durable, runtime context is not.
"""

from __future__ import annotations

import json
from typing import Any, get_type_hints

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel

from app.deep_research import graph as graph_module
from app.deep_research import llm_factory
from app.deep_research.context import (
    DEEP_RESEARCH_GRAPH_VERSION,
    DeepResearchContext,
)
from app.deep_research.state import DeepResearchState


RUNTIME_API_KEY = "sk-runtime-secret-must-not-be-checkpointed"
RUNTIME_BASE_URL = "https://private-llm.example/v1?token=runtime-secret"


def _runtime_context() -> DeepResearchContext:
    return {
        "run_id": "runtime-context-test-run",
        "workspace_id": "runtime-context-test-workspace",
        "guest_id": "runtime-context-test-guest",
        "api_key": RUNTIME_API_KEY,
        "base_url": RUNTIME_BASE_URL,
        "model": "runtime-model",
        "graph_version": DEEP_RESEARCH_GRAPH_VERSION,
    }


def test_credentials_belong_to_context_schema_not_state_schema() -> None:
    state_fields = get_type_hints(DeepResearchState)
    context_fields = get_type_hints(DeepResearchContext)

    assert "api_key" not in state_fields
    assert "base_url" not in state_fields
    assert "llm_base_url" not in state_fields
    assert context_fields["api_key"] is str
    assert "base_url" in context_fields

    graph = graph_module.build_graph()
    assert graph.state_schema is DeepResearchState
    assert graph.context_schema is DeepResearchContext


def test_api_initial_state_serialization_contains_no_runtime_credentials() -> None:
    from app.api.deep_research import (
        DeepResearchInput,
        DeepResearchRequest,
        _build_initial_state,
    )

    state = _build_initial_state(
        DeepResearchRequest(
            input=DeepResearchInput(topic="Runtime context isolation"),
            workspace_id="runtime-context-test-workspace",
        )
    )
    serialized = json.dumps(state, default=str, ensure_ascii=False)

    assert "api_key" not in serialized
    assert "base_url" not in serialized
    assert "token" not in serialized
    assert RUNTIME_API_KEY not in serialized
    assert RUNTIME_BASE_URL not in serialized


def test_make_llm_prefers_runtime_context_over_legacy_direct_fixture_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor_calls: list[dict[str, Any]] = []

    class FakeChatAnthropic:
        def __init__(self, **kwargs: Any):
            constructor_calls.append(kwargs)

    monkeypatch.setattr(llm_factory, "ChatAnthropic", FakeChatAnthropic)

    runtime = Runtime(context=_runtime_context())
    legacy_direct_fixture = {
        "api_key": "sk-stale-state-secret",
        "llm_base_url": "https://stale-state.example/v1",
        "llm_model": "stale-state-model",
    }

    model = llm_factory.make_llm(legacy_direct_fixture, runtime=runtime)

    assert isinstance(model, FakeChatAnthropic)
    assert constructor_calls == [
        {
            "model": "runtime-model",
            "api_key": RUNTIME_API_KEY,
            "max_tokens": 1500,
            "temperature": 0.3,
            "anthropic_api_url": RUNTIME_BASE_URL,
        }
    ]


def test_make_llm_keeps_direct_test_fixture_fallback_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing direct node fixtures can migrate independently of graph state."""
    constructor_calls: list[dict[str, Any]] = []

    class FakeChatAnthropic:
        def __init__(self, **kwargs: Any):
            constructor_calls.append(kwargs)

    monkeypatch.setattr(llm_factory, "ChatAnthropic", FakeChatAnthropic)

    model = llm_factory.make_llm(
        {
            "api_key": "fixture-only-key",
            "llm_base_url": "https://fixture-only.example/v1",
            "llm_model": "fixture-only-model",
        }
    )

    assert isinstance(model, FakeChatAnthropic)
    assert constructor_calls[0]["api_key"] == "fixture-only-key"
    assert constructor_calls[0]["anthropic_api_url"] == (
        "https://fixture-only.example/v1"
    )
    assert constructor_calls[0]["model"] == "fixture-only-model"


def test_make_structured_llm_forwards_runtime_to_base_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = Runtime(context=_runtime_context())
    base_factory_calls: list[dict[str, Any]] = []
    structured_calls: list[tuple[type[BaseModel], dict[str, Any]]] = []

    class ExampleSchema(BaseModel):
        answer: str

    class FakeChatModel:
        def with_structured_output(
            self,
            schema: type[BaseModel],
            **kwargs: Any,
        ) -> object:
            structured_calls.append((schema, kwargs))
            return object()

    def fake_make_llm(state: DeepResearchState, **kwargs: Any) -> FakeChatModel:
        base_factory_calls.append({"state": state, **kwargs})
        return FakeChatModel()

    monkeypatch.setattr(llm_factory, "make_llm", fake_make_llm)

    result = llm_factory.make_structured_llm(
        {},
        ExampleSchema,
        runtime=runtime,
        max_tokens=321,
        temperature=0.0,
    )

    assert result is not None
    assert len(base_factory_calls) == 1
    assert base_factory_calls[0]["runtime"] is runtime
    assert base_factory_calls[0]["max_tokens"] == 321
    assert base_factory_calls[0]["temperature"] == 0.0
    assert structured_calls == [(ExampleSchema, {})]


@pytest.mark.asyncio
async def test_compiled_graph_filters_credentials_from_state_and_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    async def fake_plan_node(
        state: DeepResearchState,
        runtime: Runtime[DeepResearchContext],
    ) -> dict[str, Any]:
        observed["plan_state"] = dict(state)
        observed["context"] = dict(runtime.context)
        return {}

    async def fake_execute_node(
        _state: DeepResearchState,
        runtime: Runtime[DeepResearchContext],
    ) -> dict[str, Any]:
        del runtime
        return {"execute_status": "failed"}

    def fake_finalize_incomplete_node(
        _state: DeepResearchState,
    ) -> dict[str, Any]:
        return {
            "terminal_status": "incomplete",
            "terminal_reason": "test_stop",
            "final_report": None,
        }

    monkeypatch.setattr(graph_module, "plan_node", fake_plan_node)
    monkeypatch.setattr(graph_module, "execute_node", fake_execute_node)
    monkeypatch.setattr(
        graph_module,
        "route_after_execute",
        lambda _state: "stop_incomplete",
    )
    monkeypatch.setattr(
        graph_module,
        "finalize_incomplete_node",
        fake_finalize_incomplete_node,
    )

    compiled = graph_module.build_graph().compile()
    result = await compiled.ainvoke(
        {
            "topic": "Runtime context isolation",
            # Unknown input keys must be filtered by the state schema before a
            # node runs, even while old API callers are being migrated.
            "api_key": "sk-input-secret-must-be-dropped",
            "base_url": "https://input-secret.example/v1?token=secret",
            "llm_base_url": "https://input-secret.example/v1?token=secret",
        },
        context=_runtime_context(),
    )

    assert observed["context"] == _runtime_context()
    assert "api_key" not in observed["plan_state"]
    assert "base_url" not in observed["plan_state"]
    assert "llm_base_url" not in observed["plan_state"]
    assert "api_key" not in result
    assert "base_url" not in result
    assert "llm_base_url" not in result

    serialized_state = json.dumps(result, default=str, ensure_ascii=False)
    assert "sk-input-secret-must-be-dropped" not in serialized_state
    assert "input-secret.example" not in serialized_state
    assert RUNTIME_API_KEY not in serialized_state
    assert RUNTIME_BASE_URL not in serialized_state


@pytest.mark.asyncio
async def test_checkpoint_excludes_context_and_graph_node_uses_runtime_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor_calls: list[dict[str, Any]] = []

    class FakeChatAnthropic:
        def __init__(self, **kwargs: Any):
            constructor_calls.append(kwargs)

    monkeypatch.setattr(llm_factory, "ChatAnthropic", FakeChatAnthropic)

    async def invoke_llm_factory(
        state: DeepResearchState,
        runtime: Runtime[DeepResearchContext],
    ) -> dict[str, Any]:
        llm_factory.make_llm(state, runtime=runtime)
        return {"terminal_status": "incomplete"}

    saver = InMemorySaver()
    builder = StateGraph(DeepResearchState, context_schema=DeepResearchContext)
    builder.add_node("invoke_llm_factory", invoke_llm_factory)
    builder.set_entry_point("invoke_llm_factory")
    builder.add_edge("invoke_llm_factory", END)
    compiled = builder.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "runtime-context-test-run"}}

    result = await compiled.ainvoke(
        {
            "topic": "Runtime context isolation",
            "api_key": "sk-input-secret-must-be-dropped",
            "base_url": "https://input-secret.example/v1?token=secret",
            "token": "input-token-must-be-dropped",
        },
        config=config,
        context=_runtime_context(),
    )
    snapshot = await compiled.aget_state(config)
    serialized_checkpoint = json.dumps(
        snapshot.values,
        default=str,
        ensure_ascii=False,
    )

    assert constructor_calls[0]["api_key"] == RUNTIME_API_KEY
    assert constructor_calls[0]["anthropic_api_url"] == RUNTIME_BASE_URL
    assert "api_key" not in result
    assert "base_url" not in result
    assert "token" not in result
    assert "api_key" not in serialized_checkpoint
    assert "base_url" not in serialized_checkpoint
    assert "token" not in serialized_checkpoint
    assert RUNTIME_API_KEY not in serialized_checkpoint
    assert RUNTIME_BASE_URL not in serialized_checkpoint
