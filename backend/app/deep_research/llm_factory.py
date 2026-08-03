from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_anthropic import ChatAnthropic
from langgraph.runtime import Runtime
from pydantic import BaseModel

from app.deep_research.context import DeepResearchContext
from app.deep_research.state import DeepResearchState

_OPENAI_COMPATIBLE_HOSTS = ("api.deepseek.com", "openrouter.ai")
StructuredModelT = TypeVar("StructuredModelT", bound=BaseModel)


def _runtime_values(
    runtime: Runtime[DeepResearchContext] | None,
    context: DeepResearchContext | None,
) -> Mapping[str, Any] | None:
    """Return invocation context without ever copying it into graph state."""
    if runtime is not None:
        runtime_context = runtime.context
        return runtime_context if isinstance(runtime_context, Mapping) else None
    if context is not None:
        return context
    return None


def _resolve_llm_settings(
    state: DeepResearchState,
    *,
    runtime: Runtime[DeepResearchContext] | None = None,
    context: DeepResearchContext | None = None,
) -> tuple[str, str | None, str | None]:
    values = _runtime_values(runtime, context)
    if values is not None:
        api_key = values.get("api_key")
        base_url = values.get("base_url")
        model = values.get("model")
    else:
        # Compatibility for direct node unit tests only. ``DeepResearchState``
        # does not declare these fields, so StateGraph drops them before a node
        # or checkpointer can observe them in a compiled workflow.
        legacy_state: Mapping[str, Any] = state
        api_key = legacy_state.get("api_key")
        base_url = legacy_state.get("llm_base_url")
        model = legacy_state.get("llm_model")

    if not isinstance(api_key, str) or not api_key:
        raise ValueError("Deep Research LLM credentials are missing from runtime context")
    if base_url is not None and not isinstance(base_url, str):
        raise ValueError("Deep Research LLM base URL is invalid")
    if model is not None and not isinstance(model, str):
        raise ValueError("Deep Research LLM model is invalid")
    return api_key, base_url, model


def resolved_model_name(
    state: DeepResearchState,
    *,
    runtime: Runtime[DeepResearchContext] | None = None,
    context: DeepResearchContext | None = None,
    default: str = "deepseek-v4-pro",
) -> str:
    """Return the configured model name without exposing runtime credentials."""
    values = _runtime_values(runtime, context)
    if values is not None:
        model = values.get("model")
    else:
        legacy_state: Mapping[str, Any] = state
        model = legacy_state.get("llm_model")
    if isinstance(model, str) and model.strip():
        return model.strip()[:200]
    return default


def _is_openai_compatible(base_url: str | None) -> bool:
    if not base_url:
        return False
    return any(host in base_url for host in _OPENAI_COMPATIBLE_HOSTS)


def _is_openrouter(base_url: str | None) -> bool:
    return bool(base_url and "openrouter.ai" in base_url)


def _is_deepseek(base_url: str | None) -> bool:
    return bool(base_url and "api.deepseek.com" in base_url)


def make_llm(
    state: DeepResearchState,
    *,
    runtime: Runtime[DeepResearchContext] | None = None,
    context: DeepResearchContext | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.3,
    model_override: str | None = None,
) -> BaseChatModel:
    api_key, base_url, configured_model = _resolve_llm_settings(
        state,
        runtime=runtime,
        context=context,
    )
    model = model_override or configured_model or "deepseek-v4-pro"

    if _is_openai_compatible(base_url):
        from langchain_openai import ChatOpenAI
        resolved_model = model
        # OpenRouter uses provider-prefixed model IDs; DeepSeek does not.
        if _is_openrouter(base_url) and "/" not in resolved_model:
            resolved_model = f"anthropic/{model.replace('-4-6', '-4.6').replace('-4-5', '-4.5').replace('-4-7', '-4.7')}"
        return ChatOpenAI(
            model=resolved_model,
            api_key=api_key,
            base_url=base_url,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    kwargs: dict = {
        "model": model,
        "api_key": api_key,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if base_url:
        kwargs["anthropic_api_url"] = base_url
    return ChatAnthropic(**kwargs)


def make_structured_llm(
    state: DeepResearchState,
    schema: type[StructuredModelT],
    *,
    runtime: Runtime[DeepResearchContext] | None = None,
    context: DeepResearchContext | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.3,
    model_override: str | None = None,
) -> Runnable:
    """Create a schema-bound model using a DeepSeek-compatible method.

    DeepSeek currently rejects OpenAI's ``json_schema`` response format, which
    is LangChain's default. Use its supported JSON-object mode and include the
    full Pydantic schema in the prompt so thinking remains available.
    """
    _, base_url, _ = _resolve_llm_settings(
        state,
        runtime=runtime,
        context=context,
    )
    is_deepseek = _is_deepseek(base_url)
    llm = make_llm(
        state,
        runtime=runtime,
        context=context,
        max_tokens=max_tokens,
        temperature=temperature,
        model_override=model_override,
    )
    if is_deepseek:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        instruction = (
            "Return only a valid JSON object matching this JSON schema. "
            "Do not wrap it in Markdown fences or add explanatory text.\n"
            f"{schema_json}"
        )

        def add_schema_instruction(messages):
            return [{"role": "system", "content": instruction}, *list(messages)]

        return (
            RunnableLambda(add_schema_instruction)
            | llm.with_structured_output(schema, method="json_mode")
        )
    return llm.with_structured_output(schema)
