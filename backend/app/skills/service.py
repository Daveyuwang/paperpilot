"""Application lifecycle and request-time facade for advisory agent skills."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from app.config import Settings, get_settings

from .models import (
    DEFAULT_CATALOG_LIMITS,
    SkillDescriptor,
    SkillRenderLimits,
)
from .registry import SkillRegistry, SkillSnapshotNotFoundError
from .source import GitSkillSource, SourceSnapshot, SourceStatus

logger = structlog.get_logger()
_MAX_RETAINED_REGISTRIES = 8

_QUERY_EXPANSIONS = (
    (
        ("论文写作", "写论文", "撰写论文", "润色论文", "修改论文", "投稿"),
        " academic paper writing conference submission",
    ),
    (("机器学习", "深度学习"), " machine learning ML AI"),
    (
        ("研究想法", "头脑风暴", "创新点"),
        " brainstorm creative research ideas ideation",
    ),
    (("微调",), " fine tuning peft lora"),
    (("量化",), " model quantization optimization"),
    (("剪枝",), " model pruning sparsity"),
    (("分布式训练",), " distributed training"),
    (("检索增强", "向量数据库"), " RAG retrieval vector database"),
    (("评测", "基准测试"), " evaluation benchmark"),
    (("推理服务",), " inference serving"),
)

_WRITING_MARKERS = (
    "write",
    "writing",
    "draft",
    "revise",
    "edit",
    "latex",
    "submit",
    "submission",
    "camera-ready",
    "conference paper",
    "写",
    "撰写",
    "润色",
    "修改",
    "投稿",
)
_IDEATION_MARKERS = (
    "brainstorm",
    "ideat",
    "novel idea",
    "research idea",
    "creative direction",
    "研究想法",
    "头脑风暴",
    "创新点",
)
_ARTIFACT_MARKERS = ("ara", "research artifact", "agent-native artifact")
_ML_PAPER_MARKERS = (
    "machine learning",
    " ml ",
    " ai ",
    "neurips",
    "icml",
    "iclr",
    "acl",
    "aaai",
    "colm",
    "机器学习",
    "深度学习",
)
_SYSTEMS_PAPER_MARKERS = ("osdi", "sosp", "asplos", "nsdi", "eurosys", "systems paper")


@dataclass(frozen=True, slots=True)
class SelectedSkillContext:
    """Metadata pinned into an agent run; it intentionally contains no body text."""

    names: tuple[str, ...] = ()
    revision: str | None = None
    scores: tuple[float, ...] = ()


class SkillService:
    """Load snapshots off the request path and route skills from memory only."""

    def __init__(
        self,
        settings: Settings,
        *,
        source: GitSkillSource | None = None,
    ) -> None:
        self.settings = settings
        self.enabled = settings.agent_skills_enabled
        if self.enabled:
            self._catalog_limits = replace(
                DEFAULT_CATALOG_LIMITS,
                max_skill_files=settings.agent_skills_max_count,
                max_file_bytes=settings.agent_skills_max_file_bytes,
            )
            self._blocked_names = frozenset(settings.agent_skills_blocked_names_list)
            self.source: GitSkillSource | None = source or GitSkillSource(
                settings.agent_skills_cache_dir,
                repository_url=settings.agent_skills_repo_url,
                ref=settings.agent_skills_repo_ref,
                refresh_interval_seconds=settings.agent_skills_refresh_seconds,
                timeout_seconds=settings.agent_skills_clone_timeout_seconds,
                lock_timeout_seconds=max(
                    30,
                    settings.agent_skills_clone_timeout_seconds * 2,
                ),
                snapshot_validator=self._validate_source_snapshot,
            )
        else:
            # The kill switch must not validate repository, ref, or numeric
            # loader settings and must never construct a network-capable source.
            self._catalog_limits = DEFAULT_CATALOG_LIMITS
            self._blocked_names = frozenset()
            self.source = None
        self._lock = threading.RLock()
        self._refresh_lock = asyncio.Lock()
        self._registries: OrderedDict[str, SkillRegistry] = OrderedDict()
        self._current_revision: str | None = None
        self._source_snapshot: SourceSnapshot | None = None
        self._state = "disabled" if not self.enabled else "empty"
        self._error: str | None = None
        self._refresh_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    def _validate_source_snapshot(self, root: Path) -> None:
        registry = SkillRegistry(
            root,
            limits=self._catalog_limits,
            blocked_skill_names=self._blocked_names,
        )
        catalog = registry.refresh()
        if not catalog.available_skills:
            raise RuntimeError("validated skill snapshot has no available skills")

    async def initialize(self) -> None:
        """Load the local pointer immediately, then refresh in the background."""

        if not self.enabled:
            return
        source = self.source
        if source is None:
            return
        self._state = "loading"
        try:
            cached = await asyncio.to_thread(source.load_current)
            if cached is not None:
                await asyncio.to_thread(self._activate, cached)
        except Exception as exc:  # noqa: BLE001 - loader failure must not stop the API
            self._record_error("skill_cache_load_failed", exc)

        if self._refresh_task is None or self._refresh_task.done():
            self._stop_event = asyncio.Event()
            self._refresh_task = asyncio.create_task(
                self._refresh_loop(),
                name="paperpilot-skill-refresh",
            )

    async def shutdown(self) -> None:
        task = self._refresh_task
        if task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._refresh_task = None

    async def refresh(self, *, force: bool = False) -> bool:
        """Refresh and atomically activate one validated snapshot."""

        if not self.enabled:
            return False
        source = self.source
        if source is None:
            return False
        async with self._refresh_lock:
            try:
                snapshot = await asyncio.to_thread(
                    source.get_snapshot,
                    force_refresh=force,
                )
                await asyncio.to_thread(self._activate, snapshot)
                return snapshot.status is not SourceStatus.STALE
            except Exception as exc:  # noqa: BLE001 - retain last-known-good state
                self._record_error("skill_refresh_failed", exc)
                return False

    async def _refresh_loop(self) -> None:
        while True:
            refreshed = await self.refresh()
            stop_event = self._stop_event
            if stop_event is None:
                return
            if refreshed:
                with self._lock:
                    source_snapshot = self._source_snapshot
                interval = max(
                    60.0,
                    float(self.settings.agent_skills_refresh_seconds),
                )
                age = (
                    max(0.0, time.time() - source_snapshot.refreshed_at)
                    if source_snapshot is not None
                    else interval
                )
                delay = max(60.0, interval - age)
            else:
                # Cold-cache network failures and cross-worker lock timeouts
                # retry promptly instead of disabling this worker for a day.
                delay = 60.0
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                continue

    def _activate(self, source_snapshot: SourceSnapshot) -> None:
        with self._lock:
            existing = self._registries.get(source_snapshot.revision)
        if existing is None:
            registry = SkillRegistry(
                source_snapshot.root,
                limits=self._catalog_limits,
                blocked_skill_names=self._blocked_names,
            )
            catalog = registry.refresh()
            if not catalog.available_skills:
                raise RuntimeError("validated skill snapshot has no available skills")
            with self._lock:
                self._registries[source_snapshot.revision] = registry
        else:
            registry = existing
            catalog = registry.current

        with self._lock:
            self._registries.move_to_end(source_snapshot.revision)
            self._current_revision = source_snapshot.revision
            self._source_snapshot = source_snapshot
            while len(self._registries) > _MAX_RETAINED_REGISTRIES:
                self._registries.popitem(last=False)
            self._state = (
                "stale" if source_snapshot.status is SourceStatus.STALE else "ready"
            )
            self._error = source_snapshot.error

        logger.info(
            "skill_snapshot_activated",
            source_revision=source_snapshot.revision,
            catalog_revision=catalog.revision,
            skill_count=len(catalog.skills),
            available_count=len(catalog.available_skills),
            blocked_count=len(catalog.blocked_skills),
            diagnostic_count=len(catalog.diagnostics),
            source_status=source_snapshot.status.value,
        )

    def _record_error(self, event: str, exc: Exception) -> None:
        message = " ".join(str(exc).splitlines())[-2_000:] or type(exc).__name__
        with self._lock:
            self._error = message
            self._state = "stale" if self._current_revision else "error"
        logger.warning(event, error=message)

    def select(self, query: str, *, flow: str) -> SelectedSkillContext:
        """Select from the in-memory catalog; this method never performs I/O."""

        if not self.enabled:
            return SelectedSkillContext()
        with self._lock:
            revision = self._current_revision
            registry = self._registries.get(revision or "")
        if registry is None or revision is None:
            return SelectedSkillContext()

        expanded_query = self._expand_query(query)
        candidates = registry.select(
            expanded_query,
            limit=max(12, self.settings.agent_skills_max_selected * 6),
            min_score=self.settings.agent_skills_min_score,
        )
        candidates = tuple(
            sorted(
                candidates,
                key=lambda item: (
                    -self._query_preference(item.skill, query),
                    -item.score,
                    item.skill.name,
                ),
            )
        )
        selections = tuple(
            item
            for item in candidates
            if self._allowed_for_flow(item.skill, query=query, flow=flow)
        )[: self.settings.agent_skills_max_selected]
        context = SelectedSkillContext(
            names=tuple(item.skill.name for item in selections),
            revision=revision,
            scores=tuple(item.score for item in selections),
        )
        if context.names:
            logger.info(
                "skills_selected",
                flow=flow,
                skill_names=list(context.names),
                skill_scores=list(context.scores),
                skill_revision=revision,
            )
        return context

    @staticmethod
    def _query_preference(skill: SkillDescriptor, query: str) -> int:
        lowered = f" {query.casefold()} "
        if skill.name == "systems-paper-writing":
            return (
                2 if any(marker in lowered for marker in _SYSTEMS_PAPER_MARKERS) else 0
            )
        if skill.name == "ml-paper-writing":
            if any(marker in lowered for marker in _ML_PAPER_MARKERS):
                return 2
            if any(marker in lowered for marker in _WRITING_MARKERS):
                return 1
        return 0

    @staticmethod
    def _expand_query(query: str) -> str:
        expanded = query
        for markers, addition in _QUERY_EXPANSIONS:
            if any(marker in query for marker in markers):
                expanded += addition
        return expanded

    @staticmethod
    def _allowed_for_flow(
        skill: SkillDescriptor,
        *,
        query: str,
        flow: str,
    ) -> bool:
        if flow != "paper_qa":
            return True
        lowered = query.casefold()
        category = skill.metadata.category
        if category == "ml-paper-writing":
            return any(marker in lowered for marker in _WRITING_MARKERS)
        if category == "research-ideation":
            return any(marker in lowered for marker in _IDEATION_MARKERS)
        if category == "agent-native-research-artifact":
            return any(marker in lowered for marker in _ARTIFACT_MARKERS)
        return True

    def render(
        self,
        names: Sequence[str],
        *,
        revision: str | None,
        max_chars: int | None = None,
    ) -> str:
        """Render pinned bodies as bounded, quoted user-data reference text."""

        if not names or not revision:
            return ""
        with self._lock:
            registry = self._registries.get(revision)
        if registry is None:
            logger.warning("skill_revision_unavailable", skill_revision=revision)
            return ""

        requested_limit = (
            max_chars
            if max_chars is not None
            else self.settings.agent_skills_max_prompt_chars
        )
        total_limit = min(
            requested_limit,
            self.settings.agent_skills_max_prompt_chars,
        )
        if total_limit < 1_024:
            logger.warning("skill_render_budget_too_small", max_chars=total_limit)
            return ""
        body_budget = max(512, total_limit - 1_500)
        per_skill = max(256, body_budget // max(1, len(names)))
        limits = SkillRenderLimits(
            max_skills=self.settings.agent_skills_max_selected,
            max_chars_per_skill=per_skill,
            max_chars_total=body_budget,
        )
        try:
            rendered = registry.render(names, limits=limits)
        except (KeyError, ValueError, SkillSnapshotNotFoundError) as exc:
            logger.warning(
                "skill_render_failed",
                skill_revision=revision,
                skill_names=list(names),
                error=str(exc),
            )
            return ""
        if len(rendered) <= total_limit:
            return rendered
        suffix = "\n[Untrusted skill reference truncated by PaperPilot.]"
        return rendered[: max(0, total_limit - len(suffix))] + suffix

    def descriptors(self) -> tuple[SkillDescriptor, ...]:
        registry = self._current_registry()
        return registry.current.skills if registry else ()

    def descriptor(self, name: str) -> SkillDescriptor | None:
        registry = self._current_registry()
        return registry.current.get(name, include_blocked=True) if registry else None

    def _current_registry(self) -> SkillRegistry | None:
        with self._lock:
            return self._registries.get(self._current_revision or "")

    def status(self) -> dict[str, Any]:
        registry = self._current_registry()
        catalog = registry.current if registry else None
        with self._lock:
            source_snapshot = self._source_snapshot
            state = self._state
            error = self._error
            revision = self._current_revision
        return {
            "enabled": self.enabled,
            "state": state,
            "source_url": self.settings.agent_skills_repo_url,
            "requested_ref": self.settings.agent_skills_repo_ref,
            "revision": revision,
            "catalog_revision": catalog.revision if catalog else None,
            "source_status": source_snapshot.status.value if source_snapshot else None,
            "refreshed_at": source_snapshot.refreshed_at if source_snapshot else None,
            "skill_count": len(catalog.skills) if catalog else 0,
            "available_count": len(catalog.available_skills) if catalog else 0,
            "blocked_count": len(catalog.blocked_skills) if catalog else 0,
            "quarantined_count": catalog.quarantined_count if catalog else 0,
            "diagnostic_count": len(catalog.diagnostics) if catalog else 0,
            "error": error,
        }

    def diagnostics(self) -> tuple[dict[str, str], ...]:
        registry = self._current_registry()
        if registry is None:
            return ()
        return tuple(
            {
                "code": item.code.value,
                "severity": item.severity.value,
                "path": item.relative_path,
                "message": item.message,
            }
            for item in registry.current.diagnostics
        )


@lru_cache
def get_skill_service() -> SkillService:
    return SkillService(get_settings())


__all__ = [
    "SelectedSkillContext",
    "SkillService",
    "get_skill_service",
]
