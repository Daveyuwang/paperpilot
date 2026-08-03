"""Durable v1 protocol helpers for the Deep Research Console."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from typing import Any
import uuid

from pydantic import BaseModel

from app.db.postgres import AsyncSessionLocal
from app.deep_research.events import (
    DEEP_RESEARCH_EVENT_SCHEMA_VERSION,
    append_run_event,
)
from app.deep_research.models import BudgetSnapshot


_EVENT_NAMESPACE = uuid.UUID("e276cb51-f70b-5bba-9d65-46885208ec5b")


def public_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): public_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_value(item) for item in value]
    return value


def utc_iso(value: datetime | None) -> str:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def serialize_run_event(event: Any) -> dict[str, Any]:
    return {
        "schema_version": event.schema_version,
        "event_id": event.event_id,
        "seq": event.seq,
        "type": event.type,
        "run_id": event.run_id,
        "emitted_at": utc_iso(event.emitted_at),
        "cycle": event.cycle,
        "plan_version": event.plan_version,
        "corpus_version": event.corpus_version,
        "report_version": event.report_version,
        "checkpoint_id": event.checkpoint_id,
        "payload": public_value(event.payload),
    }


def sse_run_event(event: Any) -> str:
    envelope = serialize_run_event(event)
    data = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.event_id}\nevent: {event.type}\ndata: {data}\n\n"


def budget_payload(value: Any) -> dict[str, int]:
    try:
        budget = (
            value
            if isinstance(value, BudgetSnapshot)
            else BudgetSnapshot.model_validate(value or {})
        )
    except (TypeError, ValueError):
        budget = BudgetSnapshot()
    return budget.model_dump(mode="json")


def artifact_ref(artifact: Any, *, include_payload: bool = True) -> dict[str, Any]:
    kind = artifact.artifact_kind
    return {
        "id": str(artifact.id),
        "run_id": str(artifact.run_id),
        "artifact_kind": kind.value if hasattr(kind, "value") else str(kind),
        "logical_artifact_id": artifact.logical_artifact_id,
        "version_number": artifact.version_number,
        "plan_version": artifact.plan_version,
        "controller_cycle": artifact.controller_cycle,
        "schema_version": artifact.schema_version,
        "parent_version_id": artifact.parent_version_id,
        "source_checkpoint_id": artifact.source_checkpoint_id,
        "content_hash": artifact.content_hash,
        "created_at": utc_iso(artifact.created_at),
        **({"payload": public_value(artifact.payload)} if include_payload else {}),
    }


class DurableRunEventWriter:
    """Append-before-send writer with deterministic replay identities."""

    def __init__(
        self,
        *,
        run_id: str,
        workspace_id: str,
        guest_id: str,
        initial_state: Mapping[str, Any] | None = None,
    ) -> None:
        self.run_id = run_id
        self.workspace_id = workspace_id
        self.guest_id = guest_id
        self.state: dict[str, Any] = dict(initial_state or {})
        self.checkpoint_id: str | None = None

    def update_state(self, update: Any) -> None:
        if isinstance(update, BaseModel):
            update = update.model_dump(mode="python")
        if isinstance(update, Mapping):
            self.state.update(dict(update))

    def _versions(self) -> tuple[int, int, int | None, int]:
        def nonnegative(name: str, default: int) -> int:
            value = self.state.get(name, default)
            return (
                value
                if isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
                else default
            )

        report_version = nonnegative("report_version", 0) or None
        pre_history = self.state.get("routing_history")
        post_history = self.state.get("post_routing_history")
        cycle = (
            len(pre_history) if isinstance(pre_history, list) else 0
        ) + (len(post_history) if isinstance(post_history, list) else 0)
        return (
            nonnegative("plan_version", 1),
            nonnegative("corpus_version", 0),
            report_version,
            cycle,
        )

    def event_id(self, event_type: str, boundary: str) -> str:
        identity = f"paperpilot|{self.run_id}|{event_type}|{boundary}"
        return str(uuid.uuid5(_EVENT_NAMESPACE, identity))

    async def append(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        boundary: str,
        checkpoint_id: str | None = None,
    ) -> Any:
        plan_version, corpus_version, report_version, cycle = self._versions()
        effective_checkpoint = checkpoint_id or self.checkpoint_id
        async with AsyncSessionLocal() as db:
            event = await append_run_event(
                db,
                run_id=self.run_id,
                workspace_id=self.workspace_id,
                guest_id=self.guest_id,
                type=event_type,
                payload=public_value(payload),
                cycle=cycle,
                plan_version=plan_version,
                corpus_version=corpus_version,
                report_version=report_version,
                checkpoint_id=effective_checkpoint,
                event_id=self.event_id(event_type, boundary),
            )
            await db.commit()
            return event


__all__ = [
    "DEEP_RESEARCH_EVENT_SCHEMA_VERSION",
    "DurableRunEventWriter",
    "artifact_ref",
    "budget_payload",
    "public_value",
    "serialize_run_event",
    "sse_run_event",
    "utc_iso",
]
