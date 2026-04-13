from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict


ProtocolType = Literal["openai", "openai_compatible", "anthropic", "gemini"]


@dataclass(frozen=True)
class ResolvedLLMSettings:
    protocol: ProtocolType
    api_key: str
    base_url: str | None = None
    model: str | None = None


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str

