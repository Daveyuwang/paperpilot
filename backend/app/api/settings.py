import structlog
from fastapi import APIRouter, Depends

from app.api.guest import require_guest_id
from app.config import get_settings
from app.db.redis_client import (
    get_guest_llm_settings,
    set_guest_llm_settings,
    delete_guest_llm_settings,
)
from app.models.schemas import LLMSettingsIn, LLMSettingsOut

logger = structlog.get_logger()
settings = get_settings()
router = APIRouter()


def _server_default_llm_settings() -> dict:
    protocol = (settings.llm_protocol or "anthropic").strip()
    base_url = settings.llm_base_url
    api_key = settings.llm_api_key

    # Backward-compat: if server default is anthropic and only ANTHROPIC_API_KEY is set
    if (not api_key) and protocol == "anthropic" and settings.anthropic_api_key:
        api_key = settings.anthropic_api_key

    # Backward-compat: map claude_model into llm_model if user didn't override
    model = settings.llm_model or settings.claude_model

    return {
        "protocol": protocol,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "language": "en",
    }


@router.get("/llm", response_model=LLMSettingsOut)
async def get_llm_settings(guest_id: str = Depends(require_guest_id)):
    stored = await get_guest_llm_settings(guest_id)
    if stored:
        return LLMSettingsOut(
            protocol=stored.get("protocol", "anthropic"),
            base_url=stored.get("base_url"),
            has_key=bool(stored.get("api_key")),
            model=stored.get("model") or "claude-sonnet-4-6",
            language=stored.get("language") or "en",
        )

    defaults = _server_default_llm_settings()
    return LLMSettingsOut(
        protocol=defaults.get("protocol", "anthropic"),
        base_url=defaults.get("base_url"),
        has_key=bool(defaults.get("api_key")),
        model=defaults.get("model") or "claude-sonnet-4-6",
        language=defaults.get("language") or "en",
    )


@router.put("/llm", response_model=LLMSettingsOut)
async def put_llm_settings(
    payload: LLMSettingsIn,
    guest_id: str = Depends(require_guest_id),
):
    protocol = payload.protocol.strip()
    base_url = payload.base_url.strip() if payload.base_url else None
    stored = await get_guest_llm_settings(guest_id)
    api_key = (payload.api_key or "").strip() or (stored.get("api_key") if stored else "")
    model = payload.model.strip() if payload.model else "claude-sonnet-4-6"
    language = payload.language.strip() if payload.language else "en"
    await set_guest_llm_settings(
        guest_id,
        {
            "protocol": protocol,
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "language": language,
        },
    )
    logger.info("guest_llm_settings_saved", guest_id=guest_id, protocol=protocol, has_base_url=bool(base_url))
    return LLMSettingsOut(protocol=protocol, base_url=base_url, has_key=bool(api_key), model=model, language=language)


@router.delete("/llm", response_model=LLMSettingsOut)
async def delete_llm_settings(guest_id: str = Depends(require_guest_id)):
    await delete_guest_llm_settings(guest_id)
    defaults = _server_default_llm_settings()
    return LLMSettingsOut(
        protocol=defaults.get("protocol", "anthropic"),
        base_url=defaults.get("base_url"),
        has_key=bool(defaults.get("api_key")),
        model=defaults.get("model") or "claude-sonnet-4-6",
        language=defaults.get("language") or "en",
    )

