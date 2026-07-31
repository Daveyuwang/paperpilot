"""Hermetic tests for the approved Git skill snapshot source."""

from __future__ import annotations

import fcntl
import json
import multiprocessing
import os
import subprocess
from pathlib import Path

import pytest

from app.skills.source import (
    DEFAULT_REPOSITORY_URL,
    DEFAULT_RETAINED_SNAPSHOTS,
    GitSkillSource,
    SourceLockTimeout,
    SourceRefreshError,
    SourceStatus,
    SourceValidationError,
)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return completed.stdout.strip()


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", "--all")
    _git(repository, "commit", "--quiet", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


@pytest.fixture
def local_upstream(tmp_path: Path) -> Path:
    repository = tmp_path / "upstream"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Fixture Author")
    _git(repository, "config", "user.email", "fixture@example.test")
    _git(repository, "branch", "-M", "main")

    skill_root = repository / "literature-review"
    references = skill_root / "references"
    scripts = skill_root / "scripts"
    assets = skill_root / "assets"
    references.mkdir(parents=True)
    scripts.mkdir()
    assets.mkdir()

    (repository / "LICENSE").write_text("fixture license\n", encoding="utf-8")
    (repository / "README.md").write_text("not activated\n", encoding="utf-8")
    (skill_root / "SKILL.md").write_text(
        "---\nname: literature-review\ndescription: Review research literature.\n---\n"
        "Use references/guide.md.\n",
        encoding="utf-8",
    )
    (references / "guide.md").write_text("Reference guide.\n", encoding="utf-8")
    (references / "raw.json").write_text('{"ignored": true}\n', encoding="utf-8")
    (scripts / "run.py").write_text(
        "raise RuntimeError('upstream code must never execute')\n",
        encoding="utf-8",
    )
    (assets / "payload.bin").write_bytes(b"\x00\x01\x02")
    (skill_root / "requirements.txt").write_text(
        "malicious-package\n", encoding="utf-8"
    )
    _commit(repository, "fixture")
    return repository


def _new_source(
    cache: Path,
    repository: Path,
    **overrides: object,
) -> GitSkillSource:
    arguments: dict[str, object] = {
        "repository_url": repository,
        "ref": "main",
        "allow_local_repository": True,
        "refresh_interval_seconds": 3_600,
        "timeout_seconds": 10,
        "lock_timeout_seconds": 2,
    }
    arguments.update(overrides)
    return GitSkillSource(cache, **arguments)  # type: ignore[arg-type]


def _hold_process_lock(lock_path: str, ready: object, release: object) -> None:
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        ready.set()  # type: ignore[attr-defined]
        release.wait(timeout=10)  # type: ignore[attr-defined]
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_refresh_builds_immutable_sanitized_snapshot(
    tmp_path: Path,
    local_upstream: Path,
) -> None:
    cache = tmp_path / "cache"
    snapshot = _new_source(cache, local_upstream).refresh()

    assert snapshot.status is SourceStatus.REFRESHED
    assert len(snapshot.revision) == 40
    assert snapshot.root == cache.resolve() / "snapshots" / snapshot.revision

    activated_files = {
        path.relative_to(snapshot.root).as_posix()
        for path in snapshot.root.rglob("*")
        if path.is_file()
    }
    assert activated_files == {
        ".paperpilot-snapshot.json",
        "LICENSE",
        "literature-review/SKILL.md",
        "literature-review/references/guide.md",
    }
    assert not (snapshot.root / ".git").exists()
    assert not (snapshot.root / "literature-review/scripts/run.py").exists()
    assert not (snapshot.root / "literature-review/requirements.txt").exists()
    assert not (snapshot.root / "literature-review/assets/payload.bin").exists()
    assert not (snapshot.root / "literature-review/references/raw.json").exists()
    assert snapshot.root.stat().st_mode & 0o222 == 0
    assert (snapshot.root / "literature-review/SKILL.md").stat().st_mode & 0o111 == 0

    marker = json.loads(
        (snapshot.root / ".paperpilot-snapshot.json").read_text(encoding="utf-8")
    )
    assert marker["source_url"] == str(local_upstream.resolve())
    assert marker["ref"] == "main"
    assert marker["revision"] == snapshot.revision

    current = json.loads((cache / "current.json").read_text(encoding="utf-8"))
    assert current["revision"] == snapshot.revision
    assert current["snapshot_path"] == f"snapshots/{snapshot.revision}"


def test_symlinks_and_executable_content_are_never_activated(
    tmp_path: Path,
    local_upstream: Path,
) -> None:
    outside = local_upstream.parent / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    references = local_upstream / "literature-review/references"
    (references / "outside.md").symlink_to(outside)
    (local_upstream / "linked-skill").mkdir()
    (local_upstream / "linked-skill/SKILL.md").symlink_to(
        local_upstream / "literature-review/SKILL.md"
    )
    _commit(local_upstream, "symlinks")

    sentinel = tmp_path / "executed"
    (local_upstream / "literature-review/scripts/run.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n",
        encoding="utf-8",
    )
    _commit(local_upstream, "executable payload")

    snapshot = _new_source(tmp_path / "cache", local_upstream).refresh()

    assert not sentinel.exists()
    assert not (snapshot.root / "literature-review/references/outside.md").exists()
    assert not (snapshot.root / "linked-skill/SKILL.md").exists()
    assert not any(path.suffix == ".py" for path in snapshot.root.rglob("*"))


def test_load_current_is_network_free_and_get_snapshot_honors_interval(
    tmp_path: Path,
    local_upstream: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1_000.0]
    source = _new_source(
        tmp_path / "cache",
        local_upstream,
        clock=lambda: now[0],
    )
    refreshed = source.refresh()

    def fail_if_git_runs(*_arguments: str) -> str:
        raise AssertionError("git must not run for a fresh cached snapshot")

    monkeypatch.setattr(source, "_git", fail_if_git_runs)
    loaded = source.load_current()
    fresh = source.get_snapshot()

    assert loaded is not None
    assert loaded.status is SourceStatus.CACHED
    assert loaded.revision == refreshed.revision
    assert fresh.status is SourceStatus.FRESH
    assert fresh.revision == refreshed.revision


def test_load_current_returns_none_before_first_refresh(
    tmp_path: Path,
    local_upstream: Path,
) -> None:
    source = _new_source(tmp_path / "cache", local_upstream)
    assert source.load_current() is None


def test_due_refresh_creates_new_version_and_keeps_old_snapshot(
    tmp_path: Path,
    local_upstream: Path,
) -> None:
    now = [100.0]
    source = _new_source(
        tmp_path / "cache",
        local_upstream,
        clock=lambda: now[0],
        refresh_interval_seconds=60,
    )
    first = source.get_snapshot()

    skill = local_upstream / "literature-review/SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "Updated.\n", encoding="utf-8")
    expected_revision = _commit(local_upstream, "update")
    now[0] += 61
    second = source.get_snapshot()

    assert second.status is SourceStatus.REFRESHED
    assert second.revision == expected_revision
    assert second.revision != first.revision
    assert first.root.is_dir()
    assert second.root.is_dir()


def test_refresh_prunes_snapshots_to_registry_retention(
    tmp_path: Path,
    local_upstream: Path,
) -> None:
    cache = tmp_path / "cache"
    source = _new_source(cache, local_upstream)
    skill = local_upstream / "literature-review/SKILL.md"
    snapshots = []

    for index in range(DEFAULT_RETAINED_SNAPSHOTS + 2):
        if index:
            skill.write_text(
                skill.read_text(encoding="utf-8") + f"Revision {index}.\n",
                encoding="utf-8",
            )
            _commit(local_upstream, f"revision {index}")
        snapshot = source.refresh()
        snapshots.append(snapshot)
        os.utime(snapshot.root, ns=(index + 1, index + 1))

    retained = {path.name for path in (cache / "snapshots").iterdir() if path.is_dir()}
    assert len(retained) == DEFAULT_RETAINED_SNAPSHOTS
    assert snapshots[-1].revision in retained
    assert all(snapshot.revision not in retained for snapshot in snapshots[:2])
    assert all(
        snapshot.revision in retained
        for snapshot in snapshots[-DEFAULT_RETAINED_SNAPSHOTS:]
    )


def test_refresh_failure_returns_stale_last_known_good_with_error(
    tmp_path: Path,
    local_upstream: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _new_source(tmp_path / "cache", local_upstream)
    first = source.refresh()

    def fail_refresh(*_arguments: str) -> str:
        raise SourceRefreshError("fixture fetch failed")

    monkeypatch.setattr(source, "_git", fail_refresh)
    stale = source.refresh()
    current = source.load_current()

    assert stale.status is SourceStatus.STALE
    assert stale.revision == first.revision
    assert stale.error == "fixture fetch failed"
    assert current is not None
    assert current.revision == first.revision


def test_failed_atomic_pointer_update_preserves_previous_current(
    tmp_path: Path,
    local_upstream: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.skills.source as source_module

    source = _new_source(tmp_path / "cache", local_upstream)
    first = source.refresh()
    skill = local_upstream / "literature-review/SKILL.md"
    skill.write_text(
        "---\nname: literature-review\ndescription: Review updated literature.\n---\nNew.\n",
        encoding="utf-8",
    )
    _commit(local_upstream, "new snapshot")

    def fail_pointer_update(_path: Path, _value: object) -> None:
        raise OSError("simulated atomic update failure")

    monkeypatch.setattr(source_module, "_atomic_write_json", fail_pointer_update)
    stale = source.refresh()

    assert stale.status is SourceStatus.STALE
    assert stale.revision == first.revision
    assert "atomic update failure" in (stale.error or "")
    current = source.load_current()
    assert current is not None
    assert current.revision == first.revision


def test_refresh_without_last_known_good_raises(
    tmp_path: Path,
    local_upstream: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _new_source(tmp_path / "cache", local_upstream)

    def fail_refresh(*_arguments: str) -> str:
        raise SourceRefreshError("fixture fetch failed")

    monkeypatch.setattr(source, "_git", fail_refresh)
    with pytest.raises(SourceRefreshError, match="fixture fetch failed"):
        source.refresh()
    assert not (tmp_path / "cache/current.json").exists()


def test_lock_is_cross_process_and_has_a_bounded_wait(
    tmp_path: Path,
    local_upstream: Path,
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_process_lock,
        args=(str(cache / ".source.lock"), ready, release),
    )
    process.start()
    try:
        assert ready.wait(timeout=5)
        source = _new_source(
            cache,
            local_upstream,
            lock_timeout_seconds=0.1,
        )
        with pytest.raises(SourceLockTimeout), source._refresh_lock():
            pass
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0


@pytest.mark.parametrize(
    "repository_url",
    [
        "http://github.com/Orchestra-Research/AI-research-SKILLs",
        "https://github.com/Orchestra-Research/another-repo",
        "https://github.com.evil.test/Orchestra-Research/AI-research-SKILLs",
        "https://user@github.com/Orchestra-Research/AI-research-SKILLs",
        "https://github.com/Orchestra-Research/AI-research-SKILLs?ref=main",
    ],
)
def test_repository_allowlist_rejects_near_matches(
    tmp_path: Path,
    repository_url: str,
) -> None:
    with pytest.raises(SourceValidationError):
        GitSkillSource(tmp_path / "cache", repository_url=repository_url)


def test_repository_allowlist_accepts_only_canonical_github_repo(
    tmp_path: Path,
) -> None:
    source = GitSkillSource(tmp_path / "cache", repository_url=DEFAULT_REPOSITORY_URL)
    assert source.repository_url == DEFAULT_REPOSITORY_URL


def test_local_repository_requires_explicit_fixture_gate(
    tmp_path: Path,
    local_upstream: Path,
) -> None:
    with pytest.raises(SourceValidationError):
        GitSkillSource(tmp_path / "cache", repository_url=local_upstream)


@pytest.mark.parametrize(
    "ref",
    ["--upload-pack=bad", "../main", "refs//main", "main.lock", "main@{1}", "a b"],
)
def test_ref_validation_rejects_unsafe_values(tmp_path: Path, ref: str) -> None:
    with pytest.raises(SourceValidationError):
        GitSkillSource(tmp_path / "cache", ref=ref)


def test_git_timeout_is_reported_without_network(
    tmp_path: Path,
    local_upstream: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _new_source(tmp_path / "cache", local_upstream, timeout_seconds=0.1)

    def time_out(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["git"], timeout=0.1)

    monkeypatch.setattr(subprocess, "run", time_out)
    with pytest.raises(SourceRefreshError, match="timed out"):
        source._git("version")


def test_malformed_refresh_preserves_restartable_last_known_good(
    tmp_path: Path,
    local_upstream: Path,
) -> None:
    cache = tmp_path / "cache"
    source = _new_source(cache, local_upstream)
    first = source.refresh()

    skill = local_upstream / "literature-review/SKILL.md"
    skill.write_text("---\nname: broken\n---\n", encoding="utf-8")
    rejected_revision = _commit(local_upstream, "malformed skill")

    stale = source.refresh()
    restarted = _new_source(cache, local_upstream).get_snapshot()

    assert stale.status is SourceStatus.STALE
    assert stale.revision == first.revision
    assert restarted.revision == first.revision
    assert not (cache / "snapshots" / rejected_revision).exists()


def test_cached_document_manifest_detects_content_tampering(
    tmp_path: Path,
    local_upstream: Path,
) -> None:
    source = _new_source(tmp_path / "cache", local_upstream)
    snapshot = source.refresh()
    skill = snapshot.root / "literature-review/SKILL.md"
    skill.chmod(0o644)
    skill.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(SourceValidationError, match="manifest"):
        source.load_current()
