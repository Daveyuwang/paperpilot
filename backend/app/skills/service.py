"""Application lifecycle and request-time facade for advisory agent skills."""

from __future__ import annotations

import asyncio
import contextlib
import re
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

from .documents import LazyDocumentCache, LoadedSkillReference, SkillDocumentError
from .models import (
    DEFAULT_CATALOG_LIMITS,
    SkillDescriptor,
    SkillRenderLimits,
    SkillSelection,
)
from .registry import SkillRegistry, SkillSnapshotNotFoundError
from .source import (
    DEFAULT_RETAINED_SNAPSHOTS,
    GitSkillSource,
    SourceSnapshot,
    SourceStatus,
)

logger = structlog.get_logger()
_MAX_RETAINED_REGISTRIES = DEFAULT_RETAINED_SNAPSHOTS

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
_ACADEMIC_PLOTTING_MARKERS = (
    "chart",
    "diagram",
    "figure",
    "matplotlib",
    "plot",
    "seaborn",
    "visualiz",
    "图表",
    "绘图",
    "可视化",
)
_CONFERENCE_TALK_MARKERS = (
    "beamer",
    "oral talk",
    "pptx",
    "presentation",
    "slide",
    "speaker note",
    "spotlight",
    "talk script",
    "幻灯片",
    "演讲",
    "汇报",
)
_SPECIALIZED_SKILL_MARKERS = {
    "academic-plotting": _ACADEMIC_PLOTTING_MARKERS,
    "presenting-conference-talks": _CONFERENCE_TALK_MARKERS,
}
_ACADEMIC_WORDS = frozenset(
    {
        "acl",
        "aaai",
        "article",
        "camera",
        "citation",
        "citations",
        "colm",
        "conference",
        "dissertation",
        "iclr",
        "icml",
        "journal",
        "manuscript",
        "neurips",
        "paper",
        "papers",
        "publication",
        "thesis",
    }
)
_ACADEMIC_CJK_MARKERS = (
    "论文",
    "稿件",
    "投稿",
    "期刊",
    "文献",
)
_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _has_writing_action(query: str) -> bool:
    lowered = query.casefold()
    words = set(_WORD_RE.findall(lowered))
    return bool(
        words
        & {
            "draft",
            "edit",
            "latex",
            "revise",
            "submission",
            "submit",
            "write",
            "writing",
        }
    ) or any(marker in lowered for marker in ("写", "撰写", "润色", "修改", "投稿"))


def _has_academic_intent(query: str) -> bool:
    lowered = query.casefold()
    return bool(set(_WORD_RE.findall(lowered)) & _ACADEMIC_WORDS) or any(
        marker in lowered for marker in _ACADEMIC_CJK_MARKERS
    )


def _has_systems_paper_intent(query: str) -> bool:
    lowered = query.casefold()
    return any(marker in lowered for marker in _SYSTEMS_PAPER_MARKERS)


@dataclass(frozen=True, slots=True)
class SelectedSkillContext:
    """Metadata pinned into an agent run; it intentionally contains no body text."""

    names: tuple[str, ...] = ()
    revision: str | None = None
    scores: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillSelectionPreview:
    """Metadata-only routing result used by the inspector UI."""

    selections: tuple[SkillSelection, ...] = ()
    source_revision: str | None = None
    catalog_revision: str | None = None


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
            self._document_cache: LazyDocumentCache | None = LazyDocumentCache(
                max_entries=settings.agent_skills_cache_max_entries,
                max_bytes=settings.agent_skills_cache_max_bytes,
            )
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
            self._document_cache = None
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
                source_revision=source_snapshot.revision,
                document_cache=self._document_cache,
                reference_max_bytes=self.settings.agent_skills_max_reference_bytes,
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

        preview = self.preview(query, flow=flow)
        context = SelectedSkillContext(
            names=tuple(item.skill.name for item in preview.selections),
            revision=preview.source_revision,
            scores=tuple(item.score for item in preview.selections),
        )
        if context.names:
            logger.info(
                "skills_selected",
                flow=flow,
                skill_names=list(context.names),
                skill_scores=list(context.scores),
                skill_revision=context.revision,
            )
        return context

    def preview(
        self,
        query: str,
        *,
        flow: str,
        limit: int | None = None,
    ) -> SkillSelectionPreview:
        """Explain runtime selection using metadata only and zero document I/O."""

        return self.preview_view(query, flow=flow, limit=limit)[0]

    def preview_view(
        self,
        query: str,
        *,
        flow: str,
        limit: int | None = None,
    ) -> tuple[SkillSelectionPreview, dict[str, Any], tuple[bool, ...]]:
        """Return a routing preview and status pinned to one catalog revision."""

        with self._lock:
            revision = self._current_revision
            registry = self._registries.get(revision or "")
            source_snapshot = self._source_snapshot
            state = self._state
            error = self._error
        if not self.enabled or registry is None or revision is None:
            status = self._status_payload(
                revision=revision,
                registry=registry,
                source_snapshot=source_snapshot,
                state=state,
                error=error,
                cache_status=self._empty_cache_status(),
            )
            return SkillSelectionPreview(), status, ()

        catalog = registry.current

        expanded_query = self._expand_query(query)
        candidates = registry.select(
            expanded_query,
            revision=catalog.revision,
            limit=max(12, self.settings.agent_skills_max_selected * 6),
            min_score=0.0,
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
        requested_limit = min(
            max(
                0,
                limit if limit is not None else self.settings.agent_skills_max_selected,
            ),
            self.settings.agent_skills_max_selected,
        )
        selections = tuple(
            item
            for item in candidates
            if self._allowed_for_flow(item.skill, query=query, flow=flow)
            and (
                item.score > self.settings.agent_skills_min_score
                or self._query_preference(item.skill, query) > 0
            )
        )[:requested_limit]
        preview = SkillSelectionPreview(
            selections=selections,
            source_revision=revision,
            catalog_revision=catalog.revision,
        )
        status = self._status_payload(
            revision=revision,
            registry=registry,
            source_snapshot=source_snapshot,
            state=state,
            error=error,
            cache_status=registry.cache_status(),
        )
        loaded = tuple(
            registry.is_loaded(item.skill.name, revision=catalog.revision)
            for item in selections
        )
        return preview, status, loaded

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
            if _has_writing_action(query) and _has_academic_intent(query):
                return 1
        return 0

    @staticmethod
    def _expand_query(query: str) -> str:
        expanded = query
        lowered = query.casefold()
        words = set(_WORD_RE.findall(lowered))
        for markers, addition in _QUERY_EXPANSIONS:
            if any(marker.casefold() in lowered for marker in markers):
                expanded += addition
        if words & {"quantize", "quantized", "quantizing"}:
            expanded += " quantization"
        if _has_writing_action(query) and _has_academic_intent(query):
            expanded += " academic writing drafting"
        return expanded

    @staticmethod
    def _allowed_for_flow(
        skill: SkillDescriptor,
        *,
        query: str,
        flow: str,
    ) -> bool:
        lowered = query.casefold()
        category = skill.metadata.category
        if skill.name == "systems-paper-writing":
            if not (_has_writing_action(query) and _has_academic_intent(query)):
                return False
            if not _has_systems_paper_intent(query):
                return False
        elif skill.name == "ml-paper-writing":
            if not (_has_writing_action(query) and _has_academic_intent(query)):
                return False
            if _has_systems_paper_intent(query):
                return False
        specialized_markers = _SPECIALIZED_SKILL_MARKERS.get(skill.name)
        if specialized_markers is not None and not any(
            marker in lowered for marker in specialized_markers
        ):
            return False
        if flow != "paper_qa":
            return True
        if category == "ml-paper-writing":
            return True
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
        except (
            KeyError,
            OSError,
            ValueError,
            SkillDocumentError,
            SkillSnapshotNotFoundError,
        ) as exc:
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

    def load_references(
        self,
        context: SelectedSkillContext,
        skill_name: str,
        paths: Sequence[str],
    ) -> tuple[LoadedSkillReference, ...]:
        """Explicitly load references for one skill selected in this exact run."""

        if (
            not self.enabled
            or not context.revision
            or skill_name not in context.names
            or not paths
        ):
            return ()
        with self._lock:
            registry = self._registries.get(context.revision)
        if registry is None:
            return ()
        unique_paths = tuple(dict.fromkeys(paths))[:8]
        try:
            return tuple(
                registry.load_reference(
                    skill_name,
                    path,
                    revision=registry.current.revision,
                )
                for path in unique_paths
            )
        except (
            KeyError,
            OSError,
            ValueError,
            SkillDocumentError,
            SkillSnapshotNotFoundError,
        ) as exc:
            logger.warning(
                "skill_reference_load_failed",
                skill_revision=context.revision,
                skill_name=skill_name,
                reference_paths=list(unique_paths),
                error=str(exc),
            )
            return ()

    def descriptors(self) -> tuple[SkillDescriptor, ...]:
        registry = self._current_registry()
        return registry.current.skills if registry else ()

    def catalog_view(
        self,
        *,
        include_items: bool = True,
    ) -> tuple[dict[str, Any], tuple[tuple[SkillDescriptor, bool], ...]]:
        """Return status and descriptors pinned to one active registry."""

        with self._lock:
            revision = self._current_revision
            registry = self._registries.get(revision or "")
            source_snapshot = self._source_snapshot
            state = self._state
            error = self._error
        catalog = registry.current if registry else None
        cache_status = (
            registry.cache_status()
            if registry is not None and self._document_cache is not None
            else self._empty_cache_status()
        )
        status = self._status_payload(
            revision=revision,
            registry=registry,
            source_snapshot=source_snapshot,
            state=state,
            error=error,
            cache_status=cache_status,
        )
        items = (
            tuple(
                (
                    skill,
                    registry.is_loaded(skill.name, revision=catalog.revision),
                )
                for skill in catalog.skills
            )
            if include_items and registry is not None and catalog is not None
            else ()
        )
        return status, items

    def descriptor_view(
        self,
        name: str,
    ) -> tuple[SkillDescriptor | None, str | None, str | None, bool]:
        """Return one descriptor and its exact source/catalog revisions."""

        with self._lock:
            source_revision = self._current_revision
            registry = self._registries.get(source_revision or "")
        if registry is None:
            return None, source_revision, None, False
        catalog = registry.current
        descriptor = catalog.get(name, include_blocked=True)
        if descriptor is None:
            return None, source_revision, catalog.revision, False
        return (
            descriptor,
            source_revision,
            catalog.revision,
            registry.is_loaded(name, revision=catalog.revision),
        )

    def descriptor(self, name: str) -> SkillDescriptor | None:
        registry = self._current_registry()
        return registry.current.get(name, include_blocked=True) if registry else None

    def is_loaded(self, name: str) -> bool:
        registry = self._current_registry()
        if registry is None:
            return False
        try:
            return registry.is_loaded(name)
        except (KeyError, SkillSnapshotNotFoundError):
            return False

    def _current_registry(self) -> SkillRegistry | None:
        with self._lock:
            return self._registries.get(self._current_revision or "")

    def status(self) -> dict[str, Any]:
        return self.status_view()[0]

    def status_view(self) -> tuple[dict[str, Any], tuple[dict[str, str], ...]]:
        """Return loader status and diagnostics from one pinned catalog."""

        with self._lock:
            revision = self._current_revision
            registry = self._registries.get(revision or "")
            source_snapshot = self._source_snapshot
            state = self._state
            error = self._error
        catalog = registry.current if registry else None
        cache_status = (
            registry.cache_status()
            if registry is not None and self._document_cache is not None
            else self._empty_cache_status()
        )
        status = self._status_payload(
            revision=revision,
            registry=registry,
            source_snapshot=source_snapshot,
            state=state,
            error=error,
            cache_status=cache_status,
        )
        diagnostics = tuple(
            {
                "code": item.code.value,
                "severity": item.severity.value,
                "path": item.relative_path,
                "message": item.message,
            }
            for item in (catalog.diagnostics if catalog else ())
        )
        return status, diagnostics

    def _empty_cache_status(self) -> dict[str, int]:
        return {
            "loaded_count": 0,
            "loaded_bytes": 0,
            "loaded_reference_count": 0,
            "cache_entry_count": 0,
            "cache_total_bytes": 0,
            "cache_max_entries": (
                self._document_cache.max_entries if self._document_cache else 0
            ),
            "cache_max_bytes": (
                self._document_cache.max_bytes if self._document_cache else 0
            ),
            "cache_hits": 0,
            "cache_misses": 0,
            "cache_evictions": 0,
        }

    def _status_payload(
        self,
        *,
        revision: str | None,
        registry: SkillRegistry | None,
        source_snapshot: SourceSnapshot | None,
        state: str,
        error: str | None,
        cache_status: dict[str, int],
    ) -> dict[str, Any]:
        catalog = registry.current if registry else None
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
            "cache_scope": "process",
            **cache_status,
        }

    def diagnostics(self) -> tuple[dict[str, str], ...]:
        return self.status_view()[1]


@lru_cache
def get_skill_service() -> SkillService:
    return SkillService(get_settings())


__all__ = [
    "SelectedSkillContext",
    "SkillSelectionPreview",
    "SkillService",
    "get_skill_service",
]
