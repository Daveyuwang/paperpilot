"""Revisioned, deterministic registry for advisory research skills."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace
from pathlib import Path

from .models import (
    DEFAULT_CATALOG_LIMITS,
    DEFAULT_RENDER_LIMITS,
    RenderedSkillReference,
    SkillAvailability,
    SkillCatalogLimits,
    SkillCatalogSnapshot,
    SkillDescriptor,
    SkillDiagnostic,
    SkillDiagnosticCode,
    SkillDiagnosticSeverity,
    SkillRenderLimits,
    SkillSelection,
)
from .parser import SkillFileError, parse_skill_file

DEFAULT_BLOCKED_SKILL_NAMES = frozenset({"autoresearch"})
_AUTORESEARCH_PATH_COMPONENTS = frozenset({"0-autoresearch-skill", "autoresearch"})
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]+", re.IGNORECASE)
_ALPHA_DIGIT_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])")
_IGNORED_DIRECTORY_NAMES = frozenset(
    {".git", ".hg", ".svn", "__pycache__", "node_modules"}
)


class SkillSnapshotNotFoundError(LookupError):
    pass


def _tokenize(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text.casefold()):
        token = match.group(0)
        tokens.append(token)
        if token.isascii():
            boundary_parts = _ALPHA_DIGIT_BOUNDARY_RE.split(token)
            if len(boundary_parts) > 1:
                tokens.extend(boundary_parts)
        elif token and ord(token[0]) >= 0x3400 and len(token) > 1:
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
    return tuple(tokens)


def _metadata_term_weights(skill: SkillDescriptor) -> Counter[str]:
    metadata = skill.metadata
    weighted: Counter[str] = Counter()
    for token in _tokenize(metadata.name.replace("-", " ")):
        weighted[token] += 5.0
    for tag in metadata.tags:
        for token in _tokenize(tag):
            weighted[token] += 3.0
    for token in _tokenize(metadata.category.replace("-", " ")):
        weighted[token] += 2.0
    for token in _tokenize(metadata.description):
        weighted[token] += 1.0
    return weighted


def select_skills(
    snapshot: SkillCatalogSnapshot,
    query: str,
    *,
    limit: int = 4,
    names: Sequence[str] | None = None,
    min_score: float = 0.0,
) -> tuple[SkillSelection, ...]:
    """Select available skills using deterministic metadata-only TF-IDF.

    ``names`` optionally restricts selection to a set pinned by a caller.  Skill
    bodies never participate in retrieval and are not rendered by this function.
    """

    if limit <= 0:
        return ()
    if min_score < 0:
        raise ValueError("min_score cannot be negative")

    if names is None:
        candidates = snapshot.available_skills
        requested_order: dict[str, int] = {}
    else:
        if isinstance(names, str):
            names = (names,)
        candidates_list: list[SkillDescriptor] = []
        requested_order = {}
        for name in names:
            if name in requested_order:
                continue
            requested_order[name] = len(requested_order)
            candidates_list.append(snapshot.require(name))
        candidates = tuple(candidates_list)

    if not candidates:
        return ()

    query_tokens = _tokenize(query)
    if not query_tokens:
        if names is None:
            return ()
        return tuple(
            SkillSelection(skill=skill, score=1.0) for skill in candidates[:limit]
        )

    query_counts = Counter(query_tokens)
    documents = {skill.name: _metadata_term_weights(skill) for skill in candidates}
    document_frequency: Counter[str] = Counter()
    for terms in documents.values():
        document_frequency.update(terms.keys())

    population = len(candidates)
    normalized_query = " ".join(query.casefold().split())
    selections: list[SkillSelection] = []
    for skill in candidates:
        terms = documents[skill.name]
        score = 0.0
        matched: list[str] = []
        for token, query_tf in query_counts.items():
            weighted_tf = terms.get(token, 0.0)
            if weighted_tf <= 0:
                continue
            inverse_frequency = (
                math.log(
                    (1.0 + population) / (1.0 + document_frequency[token]),
                )
                + 1.0
            )
            score += (
                (1.0 + math.log(query_tf)) * math.log1p(weighted_tf) * inverse_frequency
            )
            matched.append(token)

        normalized_name = skill.name.replace("-", " ")
        if normalized_query == skill.name or normalized_query == normalized_name:
            score += 12.0
        elif normalized_query and normalized_query in normalized_name:
            score += 4.0
        if score > min_score:
            selections.append(
                SkillSelection(
                    skill=skill,
                    score=round(score, 12),
                    matched_terms=tuple(sorted(set(matched))),
                ),
            )

    selections.sort(
        key=lambda item: (
            -item.score,
            requested_order.get(item.skill.name, population),
            item.skill.name,
            item.skill.relative_path,
        ),
    )
    return tuple(selections[:limit])


def _discover_skill_files(
    root: Path,
    limits: SkillCatalogLimits,
) -> tuple[list[Path], list[SkillDiagnostic]]:
    diagnostics: list[SkillDiagnostic] = []
    candidates: list[Path] = []

    try:
        if root.is_symlink():
            raise SkillFileError(
                SkillDiagnosticCode.SYMLINK_REJECTED,
                "catalog root cannot be a symlink",
                ".",
            )
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise SkillFileError(
                SkillDiagnosticCode.ROOT_INVALID,
                "catalog root is not a directory",
                ".",
            )
    except SkillFileError as exc:
        return candidates, [exc.as_diagnostic()]
    except (OSError, RuntimeError) as exc:
        return candidates, [
            SkillDiagnostic(
                code=SkillDiagnosticCode.ROOT_INVALID,
                message=f"catalog root cannot be loaded: {exc}",
                relative_path=".",
            ),
        ]

    stack = [root]
    count_limited = False
    while stack and not count_limited:
        directory = stack.pop()
        try:
            entries = sorted(
                os.scandir(directory), key=lambda item: item.name.casefold()
            )
        except OSError as exc:
            diagnostics.append(
                SkillDiagnostic(
                    code=SkillDiagnosticCode.PATH_INVALID,
                    message=f"catalog directory cannot be inspected: {exc}",
                    relative_path=directory.relative_to(root).as_posix() or ".",
                ),
            )
            continue

        child_directories: list[Path] = []
        for entry in entries:
            entry_path = Path(entry.path)
            relative_path = entry_path.relative_to(root).as_posix()
            try:
                is_symlink = entry.is_symlink()
                is_directory = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError as exc:
                diagnostics.append(
                    SkillDiagnostic(
                        code=SkillDiagnosticCode.PATH_INVALID,
                        message=f"catalog entry cannot be inspected: {exc}",
                        relative_path=relative_path,
                    ),
                )
                continue

            if is_symlink:
                diagnostics.append(
                    SkillDiagnostic(
                        code=SkillDiagnosticCode.SYMLINK_REJECTED,
                        message="symlinked catalog entries are ignored",
                        relative_path=relative_path,
                    ),
                )
                continue
            if is_directory:
                if entry.name not in _IGNORED_DIRECTORY_NAMES:
                    child_directories.append(entry_path)
                continue
            if entry.name != "SKILL.md":
                continue
            if not is_file:
                diagnostics.append(
                    SkillDiagnostic(
                        code=SkillDiagnosticCode.NOT_A_REGULAR_FILE,
                        message="SKILL.md entry is not a regular file",
                        relative_path=relative_path,
                    ),
                )
                continue
            if len(candidates) >= limits.max_skill_files:
                diagnostics.append(
                    SkillDiagnostic(
                        code=SkillDiagnosticCode.COUNT_LIMIT_EXCEEDED,
                        message=f"catalog exceeds {limits.max_skill_files} skill files",
                        relative_path=relative_path,
                    ),
                )
                count_limited = True
                break
            candidates.append(entry_path)

        stack.extend(reversed(child_directories))
    return candidates, diagnostics


def _snapshot_revision(
    skills: Sequence[SkillDescriptor],
    diagnostics: Sequence[SkillDiagnostic],
) -> str:
    payload = {
        "skills": [
            {
                "name": skill.name,
                "description": skill.metadata.description,
                "tags": skill.metadata.tags,
                "category": skill.metadata.category,
                "version": skill.metadata.version,
                "author": skill.metadata.author,
                "license": skill.metadata.license,
                "dependencies": skill.metadata.dependencies,
                "path": skill.relative_path,
                "sha256": skill.content_sha256,
                "availability": skill.availability.value,
                "blocked_reason": skill.blocked_reason,
            }
            for skill in sorted(
                skills, key=lambda item: (item.name, item.relative_path)
            )
        ],
        "diagnostics": [
            {
                "code": item.code.value,
                "path": item.relative_path,
                "severity": item.severity.value,
                "message": item.message,
            }
            for item in sorted(
                diagnostics,
                key=lambda item: (item.relative_path, item.code.value, item.message),
            )
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_skill_catalog(
    root: str | Path,
    *,
    limits: SkillCatalogLimits = DEFAULT_CATALOG_LIMITS,
    blocked_skill_names: Iterable[str] = DEFAULT_BLOCKED_SKILL_NAMES,
) -> SkillCatalogSnapshot:
    """Recursively build an immutable catalog; one bad file never aborts it."""

    root_path = Path(root).absolute()
    candidates, diagnostics = _discover_skill_files(root_path, limits)
    parsed_files = []
    for candidate in candidates:
        try:
            parsed_files.append(
                parse_skill_file(candidate, root=root_path, limits=limits)
            )
        except SkillFileError as exc:
            diagnostics.append(exc.as_diagnostic())
        except Exception as exc:  # noqa: BLE001 - isolate each untrusted document
            relative_path = candidate.name
            try:
                relative_path = candidate.relative_to(root_path.resolve()).as_posix()
            except (OSError, RuntimeError, ValueError):
                pass
            diagnostics.append(
                SkillDiagnostic(
                    code=SkillDiagnosticCode.PATH_INVALID,
                    message=f"skill file was quarantined: {exc}",
                    relative_path=relative_path,
                ),
            )

    by_name = defaultdict(list)
    for parsed in parsed_files:
        by_name[parsed.descriptor.name].append(parsed)

    duplicate_names = {name for name, entries in by_name.items() if len(entries) > 1}
    for name in sorted(duplicate_names):
        entries = sorted(by_name[name], key=lambda item: item.descriptor.relative_path)
        all_paths = ", ".join(item.descriptor.relative_path for item in entries)
        for item in entries:
            diagnostics.append(
                SkillDiagnostic(
                    code=SkillDiagnosticCode.DUPLICATE_NAME,
                    message=f"duplicate skill name {name!r}; conflicts: {all_paths}",
                    relative_path=item.descriptor.relative_path,
                ),
            )

    blocked = {name.casefold() for name in blocked_skill_names}
    skills: list[SkillDescriptor] = []
    bodies: dict[str, str] = {}
    for parsed in sorted(parsed_files, key=lambda item: item.descriptor.relative_path):
        descriptor = parsed.descriptor
        if descriptor.name in duplicate_names:
            continue
        blocks_autoresearch = "autoresearch" in blocked and any(
            part.casefold() in _AUTORESEARCH_PATH_COMPONENTS
            for part in descriptor.relative_path.split("/")[:-1]
        )
        if descriptor.name.casefold() in blocked or blocks_autoresearch:
            reason = "blocked by catalog policy; explicit enablement is required"
            descriptor = replace(
                descriptor,
                availability=SkillAvailability.BLOCKED,
                blocked_reason=reason,
            )
            diagnostics.append(
                SkillDiagnostic(
                    code=SkillDiagnosticCode.POLICY_BLOCKED,
                    message=reason,
                    relative_path=descriptor.relative_path,
                    severity=SkillDiagnosticSeverity.WARNING,
                ),
            )
        skills.append(descriptor)
        bodies[descriptor.name] = parsed.body

    skills_tuple = tuple(
        sorted(skills, key=lambda item: (item.name, item.relative_path))
    )
    diagnostics_tuple = tuple(
        sorted(
            diagnostics,
            key=lambda item: (item.relative_path, item.code.value, item.message),
        ),
    )
    revision = _snapshot_revision(skills_tuple, diagnostics_tuple)
    try:
        snapshot_root = root_path.resolve(strict=True)
    except (OSError, RuntimeError):
        snapshot_root = root_path
    return SkillCatalogSnapshot(
        root=snapshot_root,
        revision=revision,
        skills=skills_tuple,
        diagnostics=diagnostics_tuple,
        _bodies=bodies,
    )


class SkillRegistry:
    """Thread-safe facade with bounded snapshot history for pinned agent runs."""

    def __init__(
        self,
        root: str | Path,
        *,
        limits: SkillCatalogLimits = DEFAULT_CATALOG_LIMITS,
        render_limits: SkillRenderLimits = DEFAULT_RENDER_LIMITS,
        blocked_skill_names: Iterable[str] = DEFAULT_BLOCKED_SKILL_NAMES,
        history_size: int = 8,
    ) -> None:
        if history_size <= 0:
            raise ValueError("history_size must be positive")
        self.root = Path(root).absolute()
        self.limits = limits
        self.render_limits = render_limits
        self.blocked_skill_names = frozenset(blocked_skill_names)
        self.history_size = history_size
        self._lock = threading.RLock()
        self._history: OrderedDict[str, SkillCatalogSnapshot] = OrderedDict()
        self._current_revision: str | None = None

    def refresh(self) -> SkillCatalogSnapshot:
        snapshot = load_skill_catalog(
            self.root,
            limits=self.limits,
            blocked_skill_names=self.blocked_skill_names,
        )
        with self._lock:
            self._history[snapshot.revision] = snapshot
            self._history.move_to_end(snapshot.revision)
            while len(self._history) > self.history_size:
                self._history.popitem(last=False)
            self._current_revision = snapshot.revision
        return snapshot

    def snapshot(self, revision: str | None = None) -> SkillCatalogSnapshot:
        with self._lock:
            selected_revision = revision or self._current_revision
            if selected_revision is None:
                raise SkillSnapshotNotFoundError("skill catalog has not been loaded")
            try:
                return self._history[selected_revision]
            except KeyError as exc:
                raise SkillSnapshotNotFoundError(
                    f"skill catalog revision is unavailable: {selected_revision}",
                ) from exc

    @property
    def current(self) -> SkillCatalogSnapshot:
        return self.snapshot()

    def resolve(
        self,
        names: Sequence[str],
        *,
        revision: str | None = None,
    ) -> tuple[SkillDescriptor, ...]:
        snapshot = self.snapshot(revision)
        if isinstance(names, str):
            names = (names,)
        resolved: list[SkillDescriptor] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            resolved.append(snapshot.require(name))
        return tuple(resolved)

    def select(
        self,
        query: str,
        *,
        revision: str | None = None,
        names: Sequence[str] | None = None,
        limit: int = 4,
        min_score: float = 0.0,
    ) -> tuple[SkillSelection, ...]:
        return select_skills(
            self.snapshot(revision),
            query,
            names=names,
            limit=limit,
            min_score=min_score,
        )

    def render_bundle(
        self,
        names: Sequence[str],
        *,
        revision: str | None = None,
        limits: SkillRenderLimits | None = None,
    ) -> RenderedSkillReference:
        from .prompting import render_skill_references

        snapshot = self.snapshot(revision)
        return render_skill_references(
            snapshot,
            names,
            expected_revision=revision,
            limits=limits or self.render_limits,
        )

    def render(
        self,
        names: Sequence[str],
        *,
        revision: str | None = None,
        limits: SkillRenderLimits | None = None,
    ) -> str:
        return self.render_bundle(names, revision=revision, limits=limits).content
