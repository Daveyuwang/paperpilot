from __future__ import annotations

from typing import Any, NotRequired, TypedDict


DEEP_RESEARCH_GRAPH_VERSION = "deep-research.v1"


class DeepResearchContext(TypedDict):
    """Per-invocation data that must never become checkpointed graph state."""

    run_id: str
    workspace_id: str
    guest_id: str
    api_key: str
    base_url: str | None
    model: str | None
    graph_version: str
    artifact_recorder: NotRequired[Any]
    artifact_persistence_required: NotRequired[bool]
