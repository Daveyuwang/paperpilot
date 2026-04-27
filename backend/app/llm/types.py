from __future__ import annotations

from dataclasses import dataclass, field
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


# ---------------------------------------------------------------------------
# Token budget tracking
# ---------------------------------------------------------------------------


class TokenBudgetExceeded(Exception):
    pass


@dataclass
class TokenBudget:
    """Tracks token usage within a workflow run."""

    PRESETS: dict[str, int] = field(
        default_factory=lambda: {
            "quick": 50_000,
            "standard": 200_000,
            "deep": 500_000,
        },
        init=False,
        repr=False,
    )

    preset: str = "standard"
    max_tokens: int | None = None
    used: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.limit: int = self.max_tokens or self.PRESETS.get(self.preset, 200_000)

    def record(self, tokens: int) -> None:
        self.used += tokens

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exceeded(self) -> bool:
        return self.used >= self.limit

    def check(self) -> None:
        if self.exceeded:
            raise TokenBudgetExceeded(
                f"Token budget exceeded: {self.used}/{self.limit}"
            )

