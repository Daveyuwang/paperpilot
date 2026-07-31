"""Verified lazy document access and a process-local bounded LRU.

Catalog snapshots contain metadata only.  This module is the single runtime
path for opening selected skill bodies and explicitly requested Markdown
references from a pinned, immutable source snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from .models import SkillCatalogLimits, SkillDescriptor, SkillReferenceDescriptor
from .parser import parse_skill_bytes

SNAPSHOT_MARKER = ".paperpilot-snapshot.json"
_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_MANIFEST_DOCUMENTS = 2_048
_MAX_PATH_CHARS = 1_024
_MAX_PATH_DEPTH = 32
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class SkillDocumentError(RuntimeError):
    """A selected document no longer matches its validated descriptor."""


@dataclass(frozen=True, slots=True)
class SnapshotRootIdentity:
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class LoadedSkillReference:
    relative_path: str
    content_sha256: str
    content: str


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    kind: str
    skill_name: str
    text: str
    byte_size: int


DocumentCacheKey = tuple[str, str, str, str]


class LazyDocumentCache:
    """One entry-and-byte bounded LRU shared by all retained revisions."""

    def __init__(self, *, max_entries: int, max_bytes: int) -> None:
        if max_entries <= 0 or max_bytes <= 0:
            raise ValueError("lazy document cache bounds must be positive")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._lock = threading.RLock()
        self._entries: OrderedDict[DocumentCacheKey, _CacheEntry] = OrderedDict()
        self._total_bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get_or_load(
        self,
        key: DocumentCacheKey,
        *,
        kind: str,
        skill_name: str,
        loader: Callable[[], str],
    ) -> str:
        """Return a verified document, loading it once on an LRU miss.

        The lock deliberately covers the load.  Skill files are small and this
        prevents duplicate reads and temporarily exceeding either global bound.
        Failed verification is never cached.
        """

        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                self._entries.move_to_end(key)
                self._hits += 1
                return existing.text

            self._misses += 1
            text = loader()
            encoded_size = len(text.encode("utf-8"))
            if encoded_size > self.max_bytes:
                return text

            while self._entries and (
                len(self._entries) >= self.max_entries
                or self._total_bytes + encoded_size > self.max_bytes
            ):
                _, evicted = self._entries.popitem(last=False)
                self._total_bytes -= evicted.byte_size
                self._evictions += 1

            entry = _CacheEntry(
                kind=kind,
                skill_name=skill_name,
                text=text,
                byte_size=encoded_size,
            )
            self._entries[key] = entry
            self._total_bytes += encoded_size
            return text

    def contains(self, key: DocumentCacheKey) -> bool:
        with self._lock:
            return key in self._entries

    def status(
        self,
        *,
        source_revision: str | None = None,
        catalog_revision: str | None = None,
    ) -> dict[str, int]:
        """Return active-revision loading state plus global cache counters."""

        with self._lock:
            active = [
                entry
                for key, entry in self._entries.items()
                if (source_revision is None or key[0] == source_revision)
                and (catalog_revision is None or key[1] == catalog_revision)
            ]
            return {
                "loaded_count": sum(entry.kind == "skill" for entry in active),
                "loaded_bytes": sum(entry.byte_size for entry in active),
                "loaded_reference_count": sum(
                    entry.kind == "reference" for entry in active
                ),
                "cache_entry_count": len(self._entries),
                "cache_total_bytes": self._total_bytes,
                "cache_max_entries": self.max_entries,
                "cache_max_bytes": self.max_bytes,
                "cache_hits": self._hits,
                "cache_misses": self._misses,
                "cache_evictions": self._evictions,
            }


def _validate_relative_path(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str):
        raise SkillDocumentError("document path must be text")
    parsed = PurePosixPath(relative_path)
    parts = parsed.parts
    if (
        not relative_path
        or relative_path.startswith("/")
        or "\\" in relative_path
        or parsed.as_posix() != relative_path
        or len(relative_path) > _MAX_PATH_CHARS
        or len(parts) > _MAX_PATH_DEPTH
        or any(part in {"", ".", ".."} or _CONTROL_RE.search(part) for part in parts)
    ):
        raise SkillDocumentError("document path is unsafe")
    return parts


def _read_relative_regular_file(
    root: Path,
    relative_path: str,
    *,
    max_bytes: int,
    expected_root: SnapshotRootIdentity,
) -> bytes:
    """Open every path component with no-follow semantics using directory FDs."""

    if max_bytes <= 0:
        raise SkillDocumentError("document byte limit must be positive")
    parts = _validate_relative_path(relative_path)
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW

    descriptors: list[int] = []
    try:
        try:
            current = os.open(root, directory_flags)
        except OSError as exc:
            raise SkillDocumentError(f"snapshot root cannot be opened: {exc}") from exc
        descriptors.append(current)
        root_stat = os.fstat(current)
        if not stat.S_ISDIR(root_stat.st_mode):
            raise SkillDocumentError("snapshot root is not a directory")
        if (root_stat.st_dev, root_stat.st_ino) != (
            expected_root.device,
            expected_root.inode,
        ):
            raise SkillDocumentError("snapshot root identity changed after activation")

        for component in parts[:-1]:
            try:
                current = os.open(component, directory_flags, dir_fd=current)
            except OSError as exc:
                raise SkillDocumentError(
                    f"document directory cannot be opened safely: {relative_path}"
                ) from exc
            descriptors.append(current)
            if not stat.S_ISDIR(os.fstat(current).st_mode):
                raise SkillDocumentError(
                    f"document parent is not a directory: {relative_path}"
                )

        file_flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            file_flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        try:
            file_descriptor = os.open(parts[-1], file_flags, dir_fd=current)
        except OSError as exc:
            raise SkillDocumentError(
                f"document cannot be opened safely: {relative_path}"
            ) from exc
        descriptors.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SkillDocumentError(f"document is not a regular file: {relative_path}")
        if before.st_size > max_bytes:
            raise SkillDocumentError(f"document exceeds byte limit: {relative_path}")

        chunks: list[bytes] = []
        total = 0
        while total <= max_bytes:
            chunk = os.read(file_descriptor, min(64 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        data = b"".join(chunks)
        after = os.fstat(file_descriptor)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if any(getattr(before, name) != getattr(after, name) for name in stable_fields):
            raise SkillDocumentError(f"document changed while reading: {relative_path}")
        if len(data) > max_bytes or len(data) != before.st_size:
            raise SkillDocumentError(
                f"document size verification failed: {relative_path}"
            )
        return data
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _decode_utf8(data: bytes, relative_path: str) -> str:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SkillDocumentError(f"document is not UTF-8: {relative_path}") from exc
    if "\x00" in text:
        raise SkillDocumentError(f"document contains a NUL byte: {relative_path}")
    return text


def load_verified_skill_body(
    root: Path,
    descriptor: SkillDescriptor,
    *,
    limits: SkillCatalogLimits,
    expected_root: SnapshotRootIdentity,
) -> str:
    """Read and re-parse one selected body, verifying its full descriptor."""

    if not descriptor.is_available:
        raise SkillDocumentError(f"blocked skill cannot be loaded: {descriptor.name}")
    if descriptor.byte_size > limits.max_file_bytes:
        raise SkillDocumentError(f"skill exceeds configured limit: {descriptor.name}")
    data = _read_relative_regular_file(
        root,
        descriptor.relative_path,
        max_bytes=limits.max_file_bytes,
        expected_root=expected_root,
    )
    digest = hashlib.sha256(data).hexdigest()
    if digest != descriptor.content_sha256 or len(data) != descriptor.byte_size:
        raise SkillDocumentError(
            f"skill content verification failed: {descriptor.name}"
        )
    try:
        reparsed = parse_skill_bytes(
            data,
            relative_path=descriptor.relative_path,
            limits=limits,
        )
    except Exception as exc:
        raise SkillDocumentError(
            f"skill descriptor cannot be revalidated: {descriptor.name}"
        ) from exc
    observed = reparsed.descriptor
    if (
        observed.metadata != descriptor.metadata
        or observed.relative_path != descriptor.relative_path
        or observed.content_sha256 != descriptor.content_sha256
        or observed.byte_size != descriptor.byte_size
        or observed.body_chars != descriptor.body_chars
    ):
        raise SkillDocumentError(
            f"skill descriptor verification failed: {descriptor.name}"
        )
    return reparsed.body


def load_verified_markdown_reference(
    root: Path,
    reference: SkillReferenceDescriptor,
    *,
    max_bytes: int,
    expected_root: SnapshotRootIdentity,
) -> LoadedSkillReference:
    """Read one manifest-listed Markdown reference and verify its digest."""

    suffix = PurePosixPath(reference.relative_path).suffix.casefold()
    if suffix not in {".md", ".markdown"}:
        raise SkillDocumentError("only Markdown references can be loaded")
    data = _read_relative_regular_file(
        root,
        reference.relative_path,
        max_bytes=max_bytes,
        expected_root=expected_root,
    )
    digest = hashlib.sha256(data).hexdigest()
    if digest != reference.content_sha256:
        raise SkillDocumentError(
            f"reference content verification failed: {reference.relative_path}"
        )
    return LoadedSkillReference(
        relative_path=reference.relative_path,
        content_sha256=digest,
        content=_decode_utf8(data, reference.relative_path),
    )


def capture_snapshot_root(root: Path) -> SnapshotRootIdentity:
    """Pin a no-follow root identity for later openat traversal."""

    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        before = os.lstat(root)
        descriptor = os.open(root, directory_flags)
    except OSError as exc:
        raise SkillDocumentError(f"snapshot root cannot be pinned: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISDIR(before.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise SkillDocumentError("snapshot root is unsafe or changed while pinning")
    return SnapshotRootIdentity(device=opened.st_dev, inode=opened.st_ino)


def load_optional_document_manifest(
    root: Path,
    *,
    expected_root: SnapshotRootIdentity,
) -> Mapping[str, str] | None:
    """Load a strict snapshot manifest, or ``None`` for a local fixture."""

    try:
        mode = os.lstat(root / SNAPSHOT_MARKER).st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SkillDocumentError(
            f"snapshot manifest cannot be inspected: {exc}"
        ) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise SkillDocumentError("snapshot manifest is not a safe regular file")
    data = _read_relative_regular_file(
        root,
        SNAPSHOT_MARKER,
        max_bytes=_MAX_MANIFEST_BYTES,
        expected_root=expected_root,
    )
    try:
        payload = json.loads(_decode_utf8(data, SNAPSHOT_MARKER))
    except json.JSONDecodeError as exc:
        raise SkillDocumentError("snapshot manifest is invalid JSON") from exc
    documents = payload.get("documents") if isinstance(payload, dict) else None
    document_count = (
        payload.get("document_count") if isinstance(payload, dict) else None
    )
    version = payload.get("version") if isinstance(payload, dict) else None
    if (
        version != 1
        or isinstance(version, bool)
        or not isinstance(document_count, int)
        or isinstance(document_count, bool)
        or not isinstance(documents, dict)
        or document_count < 1
        or document_count != len(documents)
        or document_count > _MAX_MANIFEST_DOCUMENTS
    ):
        raise SkillDocumentError("snapshot document manifest is invalid")

    validated: dict[str, str] = {}
    for path, digest in documents.items():
        if not isinstance(path, str) or not isinstance(digest, str):
            raise SkillDocumentError("snapshot document manifest is invalid")
        _validate_relative_path(path)
        if path == SNAPSHOT_MARKER or not _SHA256_RE.fullmatch(digest):
            raise SkillDocumentError("snapshot document manifest has an unsafe entry")
        validated[path] = digest
    return MappingProxyType(validated)


__all__ = [
    "LazyDocumentCache",
    "LoadedSkillReference",
    "SkillDocumentError",
    "SnapshotRootIdentity",
    "capture_snapshot_root",
    "load_optional_document_manifest",
    "load_verified_markdown_reference",
    "load_verified_skill_body",
]
