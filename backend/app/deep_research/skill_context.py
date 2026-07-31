"""Safe prompt composition for dynamically selected research skills."""

from __future__ import annotations

from app.deep_research.state import DeepResearchState
from app.skills.prompting import attach_skill_reference, with_skill_policy
from app.skills.service import get_skill_service


def skill_aware_prompts(
    state: DeepResearchState,
    system_prompt: str,
    user_prompt: str,
    *,
    max_chars: int = 6_000,
) -> tuple[str, str]:
    """Keep PaperPilot policy in system text and upstream skill text in user data."""
    reference = get_skill_service().render(
        state.get("skill_names", []),
        revision=state.get("skill_revision"),
        max_chars=max_chars,
    )
    if not reference:
        return system_prompt, user_prompt
    return with_skill_policy(system_prompt), attach_skill_reference(
        user_prompt, reference
    )
