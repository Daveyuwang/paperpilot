"""Safe, immutable snapshots of the approved research-skills repository.

This module only retrieves repository data.  It deliberately does not import,
install, or execute anything from the upstream checkout.  Activated snapshots
contain SKILL.md documents, Markdown references, the root LICENSE, and source
metadata; scripts, dependencies, assets, Git metadata, and binaries remain in
an ephemeral checkout that is deleted before the snapshot is published.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from math import isfinite
from pathlib import Path
from urllib.parse import unquote, urlsplit

import structlog

DEFAULT_REPOSITORY_URL = "https://github.com/Orchestra-Research/AI-research-SKILLs"
DEFAULT_REF = "main"
DEFAULT_REFRESH_INTERVAL_SECONDS = 60 * 60
DEFAULT_COMMAND_TIMEOUT_SECONDS = 30
DEFAULT_LOCK_TIMEOUT_SECONDS = 30
DEFAULT_RETAINED_SNAPSHOTS = 8

logger = structlog.get_logger()

_METADATA_VERSION = 1
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_MAX_METADATA_BYTES = 256 * 1024
_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
_MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
_MAX_SNAPSHOT_FILES = 2_048
_SNAPSHOT_MARKER = ".paperpilot-snapshot.json"


class SkillSourceError(RuntimeError):
    """Base error raised by the skill source."""


class SourceValidationError(SkillSourceError):
    """The repository, ref, cache metadata, or checked-out data is unsafe."""


class SourceRefreshError(SkillSourceError):
    """A repository refresh could not be completed."""


class SourceLockTimeout(SourceRefreshError):
    """Another process held the source refresh lock for too long."""


class SourceStatus(str, Enum):
    """How the returned snapshot was obtained."""

    CACHED = "cached"
    FRESH = "fresh"
    REFRESHED = "refreshed"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """An immutable, sanitized snapshot ready for registry discovery."""

    root: Path
    revision: str
    status: SourceStatus
    refreshed_at: float
    source_url: str
    ref: str
    error: str | None = None


class GitSkillSource:
    """Maintain last-known-good snapshots of the one approved GitHub repo.

    ``allow_local_repository`` exists solely to support hermetic tests and
    controlled development fixtures.  Production callers should leave it
    disabled, which enforces the exact HTTPS GitHub repository allowlist.
    """

    def __init__(
        self,
        cache_dir: str | os.PathLike[str],
        *,
        repository_url: str | os.PathLike[str] = DEFAULT_REPOSITORY_URL,
        ref: str = DEFAULT_REF,
        refresh_interval_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
        allow_local_repository: bool = False,
        clock: Callable[[], float] = time.time,
        snapshot_validator: Callable[[Path], None] | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir).expanduser().resolve(strict=False)
        self.repository_url = _validate_repository_url(
            repository_url,
            allow_local=allow_local_repository,
        )
        self.ref = _validate_ref(ref)
        self.refresh_interval_seconds = _positive_or_zero(
            refresh_interval_seconds,
            "refresh_interval_seconds",
        )
        self.timeout_seconds = _positive(timeout_seconds, "timeout_seconds")
        self.lock_timeout_seconds = _positive(
            lock_timeout_seconds,
            "lock_timeout_seconds",
        )
        self._allow_local_repository = allow_local_repository
        self._clock = clock
        self._snapshot_validator = snapshot_validator

    def load_current(self) -> SourceSnapshot | None:
        """Load the last-known-good snapshot without locking or network I/O."""

        current_path = _safe_child(self.cache_dir, "current.json")
        try:
            metadata = _read_json(current_path)
        except FileNotFoundError:
            return None

        _validate_metadata_identity(
            metadata,
            source_url=self.repository_url,
            ref=self.ref,
            label="current",
        )
        revision = _validate_revision(metadata.get("revision"))
        expected_relative = f"snapshots/{revision}"
        if metadata.get("snapshot_path") != expected_relative:
            raise SourceValidationError("current snapshot path is invalid")
        refreshed_at = _validate_timestamp(metadata.get("refreshed_at"))

        snapshot_root = _safe_child(self.cache_dir, "snapshots", revision)
        self._validate_snapshot(snapshot_root, revision)
        return SourceSnapshot(
            root=snapshot_root,
            revision=revision,
            status=SourceStatus.CACHED,
            refreshed_at=refreshed_at,
            source_url=self.repository_url,
            ref=self.ref,
        )

    def get_snapshot(self, *, force_refresh: bool = False) -> SourceSnapshot:
        """Return a usable snapshot, refreshing it when due.

        Refresh failures never replace ``current.json``.  When a valid current
        snapshot exists it is returned with ``status=stale`` and the refresh
        error attached.  With no last-known-good snapshot, the failure is
        raised to the caller.
        """

        current, current_error = self._try_load_current()
        if (
            current is not None
            and not force_refresh
            and self._is_within_refresh_interval(current)
        ):
            return replace(current, status=SourceStatus.FRESH)

        try:
            with self._refresh_lock():
                # Another process may have completed a refresh while this one
                # was waiting for the lock, so read the atomic pointer again.
                current, current_error = self._try_load_current()
                if (
                    current is not None
                    and not force_refresh
                    and self._is_within_refresh_interval(current)
                ):
                    return replace(current, status=SourceStatus.FRESH)
                return self._refresh_locked()
        except Exception as exc:
            if current is not None:
                return replace(
                    current,
                    status=SourceStatus.STALE,
                    error=_safe_error_message(exc),
                )
            if isinstance(exc, SkillSourceError):
                if current_error is not None and current_error is not exc:
                    raise SourceRefreshError(
                        "refresh failed and cached metadata is invalid: "
                        f"{_safe_error_message(exc)}"
                    ) from exc
                raise
            raise SourceRefreshError(_safe_error_message(exc)) from exc

    def refresh(self) -> SourceSnapshot:
        """Force an upstream check while retaining last-known-good fallback."""

        return self.get_snapshot(force_refresh=True)

    def _try_load_current(
        self,
    ) -> tuple[SourceSnapshot | None, Exception | None]:
        try:
            return self.load_current(), None
        except (OSError, SkillSourceError, ValueError) as exc:
            return None, exc

    def _is_within_refresh_interval(self, snapshot: SourceSnapshot) -> bool:
        age = max(0.0, self._clock() - snapshot.refreshed_at)
        return age < self.refresh_interval_seconds

    def _refresh_locked(self) -> SourceSnapshot:
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        snapshots_dir = _safe_child(self.cache_dir, "snapshots")
        snapshots_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        stage_path = Path(tempfile.mkdtemp(prefix=".source-stage-", dir=self.cache_dir))
        checkout_root = _safe_child(stage_path, "checkout")
        sanitized_root = _safe_child(stage_path, "snapshot")
        checkout_root.mkdir(mode=0o700)
        sanitized_root.mkdir(mode=0o700)

        try:
            self._git("init", "--quiet", str(checkout_root))
            self._git(
                "-C",
                str(checkout_root),
                "fetch",
                "--quiet",
                "--depth=1",
                "--no-tags",
                "--no-recurse-submodules",
                self.repository_url,
                self.ref,
            )
            self._git(
                "-C",
                str(checkout_root),
                "checkout",
                "--quiet",
                "--detach",
                "FETCH_HEAD",
            )
            revision = self._git(
                "-C",
                str(checkout_root),
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
            ).strip()
            revision = _validate_revision(revision)

            refreshed_at = self._clock()
            file_count = _build_sanitized_snapshot(
                checkout_root,
                sanitized_root,
                source_url=self.repository_url,
                ref=self.ref,
                revision=revision,
                refreshed_at=refreshed_at,
            )
            if file_count < 1:
                raise SourceValidationError(
                    "upstream repository contains no regular SKILL.md files"
                )

            target_root = _safe_child(snapshots_dir, revision)
            if target_root.exists() or target_root.is_symlink():
                self._validate_snapshot(target_root, revision)
                _make_tree_read_only(target_root)
            else:
                self._validate_snapshot(sanitized_root, revision)
                # macOS cannot rename a directory after its own mode is 0555,
                # so seal every child first, publish it, then seal the root.
                # Any ordinary failure removes the unpublished target; a
                # process crash is recovered by the existing-target path.
                _make_tree_read_only(sanitized_root, seal_root=False)
                os.replace(sanitized_root, target_root)
                try:
                    target_root.chmod(0o555)
                except OSError:
                    _remove_tree(target_root)
                    raise
                _fsync_directory(snapshots_dir)

            current_metadata = {
                "version": _METADATA_VERSION,
                "source_url": self.repository_url,
                "ref": self.ref,
                "revision": revision,
                "snapshot_path": f"snapshots/{revision}",
                "refreshed_at": refreshed_at,
            }
            _atomic_write_json(
                _safe_child(self.cache_dir, "current.json"),
                current_metadata,
            )
            try:
                self._prune_snapshots(snapshots_dir, current_revision=revision)
            except OSError as exc:
                # Cleanup must never invalidate a snapshot that was already
                # published atomically. A later refresh will retry pruning.
                logger.warning(
                    "skill_snapshot_prune_failed",
                    error=_safe_error_message(exc),
                )
            return SourceSnapshot(
                root=target_root,
                revision=revision,
                status=SourceStatus.REFRESHED,
                refreshed_at=refreshed_at,
                source_url=self.repository_url,
                ref=self.ref,
            )
        finally:
            _remove_tree(stage_path)

    @staticmethod
    def _prune_snapshots(snapshots_dir: Path, *, current_revision: str) -> None:
        """Keep the current snapshot plus the newest retained revisions."""

        candidates: list[tuple[int, str, Path]] = []
        for entry in os.scandir(snapshots_dir):
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
                if not _REVISION_RE.fullmatch(entry.name):
                    continue
                modified = entry.stat(follow_symlinks=False).st_mtime_ns
            except OSError:
                continue
            candidates.append((modified, entry.name, Path(entry.path)))

        candidates.sort(reverse=True)
        keep = {current_revision}
        for _modified, revision, _path in candidates:
            if revision == current_revision:
                continue
            if len(keep) >= DEFAULT_RETAINED_SNAPSHOTS:
                break
            keep.add(revision)

        removed = False
        for _modified, revision, path in candidates:
            if revision in keep:
                continue
            _remove_tree(path)
            removed = True
        if removed:
            _fsync_directory(snapshots_dir)

    def _validate_snapshot(self, root: Path, revision: str) -> None:
        if root.is_symlink() or not root.is_dir():
            raise SourceValidationError("snapshot root is missing or unsafe")
        metadata = _read_json(_safe_child(root, _SNAPSHOT_MARKER))
        _validate_metadata_identity(
            metadata,
            source_url=self.repository_url,
            ref=self.ref,
            label="snapshot",
        )
        if _validate_revision(metadata.get("revision")) != revision:
            raise SourceValidationError("snapshot revision does not match path")
        _validate_document_manifest(root, metadata)
        _validate_loadable_catalog(root)
        if self._snapshot_validator is not None:
            try:
                self._snapshot_validator(root)
            except Exception as exc:
                raise SourceValidationError(
                    f"snapshot failed application validation: {_safe_error_message(exc)}"
                ) from exc

    def _git(self, *arguments: str) -> str:
        command = [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "protocol.ssh.allow=never",
            "-c",
            "protocol.git.allow=never",
            "-c",
            "protocol.http.allow=never",
            "-c",
            "protocol.https.allow=always",
            "-c",
            (
                "protocol.file.allow=always"
                if self._allow_local_repository
                else "protocol.file.allow=never"
            ),
            *arguments,
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                env=_git_environment(),
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SourceRefreshError(
                f"git command timed out after {self.timeout_seconds:g} seconds"
            ) from exc
        except OSError as exc:
            raise SourceRefreshError(f"could not start git: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            detail = _single_line(detail)[-1_000:]
            message = f"git command failed with exit code {completed.returncode}"
            if detail:
                message = f"{message}: {detail}"
            raise SourceRefreshError(message)
        return completed.stdout

    @contextmanager
    def _refresh_lock(self) -> Iterator[None]:
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = _safe_child(self.cache_dir, ".source.lock")
        flags = os.O_CREAT | os.O_RDWR
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EMLINK}:
                raise SourceValidationError("source lock path is unsafe") from exc
            raise
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise SourceValidationError("source lock must be a regular file")

        deadline = time.monotonic() + self.lock_timeout_seconds
        acquired = False
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise SourceLockTimeout(
                            "timed out waiting for the source refresh lock"
                        )
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            yield
        finally:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _validate_repository_url(
    repository_url: str | os.PathLike[str],
    *,
    allow_local: bool,
) -> str:
    raw = os.fspath(repository_url)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise SourceValidationError("repository URL is invalid")

    parsed = urlsplit(raw)
    if allow_local and parsed.scheme in {"", "file"}:
        if parsed.scheme == "file":
            if parsed.netloc not in {"", "localhost"}:
                raise SourceValidationError("local file URL host is invalid")
            local_value = unquote(parsed.path)
        else:
            local_value = raw
        local_path = Path(local_value).expanduser().resolve(strict=True)
        if not local_path.is_dir():
            raise SourceValidationError("local repository must be a directory")
        return str(local_path)

    try:
        port = parsed.port
    except ValueError as exc:
        raise SourceValidationError("repository URL port is invalid") from exc
    allowed_paths = {
        "/Orchestra-Research/AI-research-SKILLs",
        "/Orchestra-Research/AI-research-SKILLs.git",
    }
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path not in allowed_paths
        or parsed.query
        or parsed.fragment
    ):
        raise SourceValidationError(
            "repository must be the approved Orchestra-Research HTTPS URL"
        )
    return DEFAULT_REPOSITORY_URL


def _validate_ref(ref: str) -> str:
    if not isinstance(ref, str) or not _ALLOWED_REF_RE.fullmatch(ref):
        raise SourceValidationError("git ref contains unsupported characters")
    if (
        ".." in ref
        or "//" in ref
        or "@{" in ref
        or ref.endswith((".", "/"))
        or any(
            component.startswith(".") or component.endswith(".lock")
            for component in ref.split("/")
        )
    ):
        raise SourceValidationError("git ref has an unsafe structure")
    return ref


def _validate_revision(value: object) -> str:
    if not isinstance(value, str) or not _REVISION_RE.fullmatch(value):
        raise SourceValidationError("resolved revision is not a 40-character SHA")
    return value


def _validate_timestamp(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SourceValidationError("snapshot refresh timestamp is invalid")
    result = float(value)
    if result < 0 or not isfinite(result):
        raise SourceValidationError("snapshot refresh timestamp is invalid")
    return result


def _validate_metadata_identity(
    metadata: object,
    *,
    source_url: str,
    ref: str,
    label: str,
) -> None:
    if not isinstance(metadata, dict) or metadata.get("version") != _METADATA_VERSION:
        raise SourceValidationError(f"{label} metadata version is invalid")
    if metadata.get("source_url") != source_url or metadata.get("ref") != ref:
        raise SourceValidationError(f"{label} metadata source does not match")


def _build_sanitized_snapshot(
    checkout_root: Path,
    destination_root: Path,
    *,
    source_url: str,
    ref: str,
    revision: str,
    refreshed_at: float,
) -> int:
    skill_documents = sorted(_find_skill_documents(checkout_root))
    if not skill_documents:
        return 0

    selected: set[Path] = set(skill_documents)
    license_path = _safe_child(checkout_root, "LICENSE")
    if _is_regular_file_without_symlink(license_path):
        selected.add(license_path)

    for skill_document in skill_documents:
        references_root = skill_document.parent / "references"
        if references_root.is_symlink() or not references_root.is_dir():
            continue
        for path in _walk_regular_files(references_root):
            if path.suffix.lower() in {".md", ".markdown"}:
                selected.add(path)

    total_bytes = 0
    copied_count = 0
    document_hashes: dict[str, str] = {}
    for source_path in sorted(selected):
        relative_path = _safe_relative_path(source_path, checkout_root)
        data = _read_regular_file(source_path, _MAX_DOCUMENT_BYTES)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceValidationError(
                f"selected document is not UTF-8: {relative_path.as_posix()}"
            ) from exc
        total_bytes += len(data)
        copied_count += 1
        if total_bytes > _MAX_SNAPSHOT_BYTES or copied_count > _MAX_SNAPSHOT_FILES:
            raise SourceValidationError("sanitized snapshot exceeds safety limits")
        _write_document(_safe_child(destination_root, *relative_path.parts), data)
        document_hashes[relative_path.as_posix()] = hashlib.sha256(data).hexdigest()

    marker = {
        "version": _METADATA_VERSION,
        "source_url": source_url,
        "ref": ref,
        "revision": revision,
        "refreshed_at": refreshed_at,
        "document_count": copied_count,
        "documents": document_hashes,
    }
    _write_json(_safe_child(destination_root, _SNAPSHOT_MARKER), marker, mode=0o444)
    return len(skill_documents)


def _validate_document_manifest(root: Path, metadata: object) -> None:
    """Verify the complete sanitized tree before it can be activated."""

    if not isinstance(metadata, dict):
        raise SourceValidationError("snapshot metadata is invalid")
    documents = metadata.get("documents")
    document_count = metadata.get("document_count")
    if (
        not isinstance(documents, dict)
        or not isinstance(document_count, int)
        or isinstance(document_count, bool)
        or document_count != len(documents)
        or document_count < 1
        or document_count > _MAX_SNAPSHOT_FILES
    ):
        raise SourceValidationError("snapshot document manifest is invalid")

    expected: dict[str, str] = {}
    for relative_name, digest in documents.items():
        if not isinstance(relative_name, str) or not isinstance(digest, str):
            raise SourceValidationError("snapshot document manifest is invalid")
        relative_path = Path(relative_name)
        if (
            not relative_name
            or relative_name == _SNAPSHOT_MARKER
            or relative_path.is_absolute()
            or relative_path.as_posix() != relative_name
            or any(part in {"", ".", ".."} for part in relative_path.parts)
            or not _SHA256_RE.fullmatch(digest)
        ):
            raise SourceValidationError(
                "snapshot document manifest contains an unsafe entry"
            )
        expected[relative_name] = digest

    actual: dict[str, str] = {}
    total_bytes = 0
    for directory, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        directory_path = Path(directory)
        for directory_name in directory_names:
            if (directory_path / directory_name).is_symlink():
                raise SourceValidationError("snapshot contains a symlinked directory")
        for file_name in file_names:
            path = directory_path / file_name
            if path.is_symlink() or not _is_regular_file_without_symlink(path):
                raise SourceValidationError("snapshot contains an unsafe file")
            relative_name = _safe_relative_path(path, root).as_posix()
            if relative_name == _SNAPSHOT_MARKER:
                continue
            if relative_name not in expected:
                raise SourceValidationError(
                    "snapshot contains an unmanifested document"
                )
            data = _read_regular_file(path, _MAX_DOCUMENT_BYTES)
            total_bytes += len(data)
            if total_bytes > _MAX_SNAPSHOT_BYTES:
                raise SourceValidationError("sanitized snapshot exceeds safety limits")
            actual[relative_name] = hashlib.sha256(data).hexdigest()

    if actual != expected:
        raise SourceValidationError("snapshot documents do not match their manifest")


def _validate_loadable_catalog(root: Path) -> None:
    """Reject a source revision that contains no usable skill entrypoint."""

    # Imported lazily to keep the source module independent during import and
    # avoid running any third-party content.  The registry parser is stdlib-only.
    from .registry import load_skill_catalog

    catalog = load_skill_catalog(root)
    if not catalog.available_skills:
        raise SourceValidationError("snapshot contains no valid available skills")


def _find_skill_documents(root: Path) -> Iterator[Path]:
    for path in _walk_regular_files(root, ignored_directories={".git"}):
        if path.name == "SKILL.md":
            yield path


def _walk_regular_files(
    root: Path,
    *,
    ignored_directories: set[str] | None = None,
) -> Iterator[Path]:
    ignored = ignored_directories or set()
    for directory, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        directory_path = Path(directory)
        directory_names[:] = [
            name
            for name in directory_names
            if name not in ignored and not (directory_path / name).is_symlink()
        ]
        for file_name in file_names:
            path = directory_path / file_name
            if _is_regular_file_without_symlink(path):
                yield path


def _is_regular_file_without_symlink(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _safe_relative_path(path: Path, root: Path) -> Path:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise SourceValidationError("selected document escapes checkout root") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise SourceValidationError("selected document has an unsafe path")
    return relative


def _read_regular_file(path: Path, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise SourceValidationError(f"could not safely read {path.name}") from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > maximum_bytes:
            raise SourceValidationError(f"selected document is unsafe: {path.name}")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > maximum_bytes:
            raise SourceValidationError(f"selected document is too large: {path.name}")
        return data
    finally:
        os.close(descriptor)


def _write_document(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, 0o444)
    finally:
        os.close(descriptor)


def _make_tree_read_only(root: Path, *, seal_root: bool = True) -> None:
    for directory, directory_names, file_names in os.walk(root, topdown=False):
        directory_path = Path(directory)
        for file_name in file_names:
            os.chmod(directory_path / file_name, 0o444)
        for directory_name in directory_names:
            os.chmod(directory_path / directory_name, 0o555)
        if seal_root or directory_path != root:
            os.chmod(directory_path, 0o555)


def _read_json(path: Path) -> object:
    data = _read_regular_file(path, _MAX_METADATA_BYTES)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceValidationError(f"invalid JSON metadata: {path.name}") from exc


def _write_json(path: Path, value: object, *, mode: int) -> None:
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _atomic_write_json(path: Path, value: object) -> None:
    temporary_path = _safe_child(
        path.parent,
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp",
    )
    try:
        _write_json(temporary_path, value, mode=0o600)
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _safe_child(root: Path, *parts: str) -> Path:
    if any(
        not isinstance(part, str)
        or part in {"", ".", ".."}
        or Path(part).is_absolute()
        or len(Path(part).parts) != 1
        for part in parts
    ):
        raise SourceValidationError("unsafe cache path component")
    candidate = root.joinpath(*parts)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SourceValidationError("cache path escapes cache root") from exc
    return candidate


def _git_environment() -> dict[str, str]:
    allowed_keys = {
        "PATH",
        "LANG",
        "LC_ALL",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "NO_PROXY",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    environment = {
        key: value for key, value in os.environ.items() if key in allowed_keys
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
        }
    )
    return environment


def _remove_tree(root: Path) -> None:
    """Remove one validated private tree without following symlinks."""

    if not root.exists() and not root.is_symlink():
        return
    if root.is_symlink() or not root.is_dir():
        root.unlink()
        return
    # Writable directories are required to unlink their children on POSIX.
    # Do this in a separate top-down pass because a failed publish may leave
    # a partly sealed staging tree.
    for directory, directory_names, _file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        directory_path = Path(directory)
        directory_path.chmod(0o700)
        directory_names[:] = [
            name for name in directory_names if not (directory_path / name).is_symlink()
        ]

    for directory, directory_names, file_names in os.walk(
        root,
        topdown=False,
        followlinks=False,
    ):
        directory_path = Path(directory)
        for file_name in file_names:
            path = directory_path / file_name
            # Unlinking only needs a writable parent directory.  Never chmod a
            # checkout entry here: Path.chmod follows symlinks and an upstream
            # symlink could otherwise change permissions outside this private
            # staging tree.
            path.unlink()
        for directory_name in directory_names:
            path = directory_path / directory_name
            if path.is_symlink():
                path.unlink()
            else:
                path.chmod(0o700)
                path.rmdir()
    root.rmdir()


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _positive(value: float, name: str) -> float:
    result = _positive_or_zero(value, name)
    if result == 0:
        raise SourceValidationError(f"{name} must be greater than zero")
    return result


def _positive_or_zero(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise SourceValidationError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SourceValidationError(f"{name} must be a finite number") from exc
    if result < 0 or not isfinite(result):
        raise SourceValidationError(f"{name} must be a finite non-negative number")
    return result


def _safe_error_message(error: BaseException) -> str:
    message = _single_line(str(error)).strip()
    return message[-2_000:] or type(error).__name__


def _single_line(value: str) -> str:
    return " ".join(value.replace("\x00", "").splitlines())


__all__ = [
    "DEFAULT_REF",
    "DEFAULT_REPOSITORY_URL",
    "GitSkillSource",
    "SkillSourceError",
    "SourceLockTimeout",
    "SourceRefreshError",
    "SourceSnapshot",
    "SourceStatus",
    "SourceValidationError",
]
