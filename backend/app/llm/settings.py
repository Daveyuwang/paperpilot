from __future__ import annotations

from app.config import get_settings
from app.db.redis_client import get_guest_llm_settings
from app.llm.types import ResolvedLLMSettings, ProtocolType


settings = get_settings()


def _normalize_protocol(value: str | None) -> ProtocolType:
    raw = (value or "anthropic").strip().lower()
    if raw in ("openai",):
        return "openai"
    if raw in ("openai_compatible", "openai-compatible", "router"):
        return "openai_compatible"
    if raw in ("anthropic", "claude"):
        return "anthropic"
    if raw in ("gemini", "google"):
        return "gemini"
    return "anthropic"


async def resolve_llm_settings_for_guest(guest_id: str) -> ResolvedLLMSettings:
    if guest_id:
        stored = await get_guest_llm_settings(guest_id)
        if stored and stored.get("api_key"):
            return ResolvedLLMSettings(
                protocol=_normalize_protocol(stored.get("protocol")),
                base_url=stored.get("base_url"),
                api_key=str(stored.get("api_key") or ""),
                model=stored.get("model"),
            )

    protocol = _normalize_protocol(settings.llm_protocol)
    api_key = settings.llm_api_key
    base_url = settings.llm_base_url

    if not api_key and protocol == "anthropic" and settings.anthropic_api_key:
        api_key = settings.anthropic_api_key

    return ResolvedLLMSettings(
        protocol=protocol,
        base_url=base_url,
        api_key=api_key,
        model=settings.llm_model or settings.claude_model,
    )

