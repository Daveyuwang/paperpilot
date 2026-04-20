from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_anthropic import ChatAnthropic

from app.deep_research.state import DeepResearchState

_OPENROUTER_HOSTS = ("openrouter.ai",)


def _is_openai_compatible(base_url: str | None) -> bool:
    if not base_url:
        return False
    return any(host in base_url for host in _OPENROUTER_HOSTS)


def make_llm(
    state: DeepResearchState,
    *,
    max_tokens: int = 1500,
    temperature: float = 0.3,
    model_override: str | None = None,
) -> BaseChatModel:
    api_key = state["api_key"]
    base_url = state.get("llm_base_url")
    model = model_override or state.get("llm_model") or "claude-sonnet-4-6"

    if _is_openai_compatible(base_url):
        from langchain_openai import ChatOpenAI
        # OpenRouter uses "anthropic/claude-sonnet-4.6" format
        or_model = model
        if "/" not in or_model:
            or_model = f"anthropic/{model.replace('-4-6', '-4.6').replace('-4-5', '-4.5').replace('-4-7', '-4.7')}"
        return ChatOpenAI(
            model=or_model,
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
