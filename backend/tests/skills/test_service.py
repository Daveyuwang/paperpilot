"""Request-path tests for the in-memory skill service facade."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.skills.service import SkillService
from app.skills.source import SourceSnapshot, SourceStatus


class NoIoSource:
    """A source double that fails if request-time code consults the source."""

    def load_current(self):
        raise AssertionError("select/render must not load source state")

    def get_snapshot(self, **_kwargs):
        raise AssertionError("select/render must not fetch source state")


def test_select_is_zero_io_and_render_lazily_reads_selected_body(
    tmp_path: Path,
    write_skill,
    monkeypatch,
) -> None:
    skill_path = write_skill(
        tmp_path,
        "methods/review",
        name="literature-review",
        description="Systematic literature review and paper synthesis",
        tags=("papers", "review"),
        body="PINNED LOCAL GUIDANCE\n",
    )
    settings = Settings(
        agent_skills_enabled=True,
        agent_skills_cache_dir=str(tmp_path / "unused-cache"),
        agent_skills_max_selected=2,
        agent_skills_max_prompt_chars=4_000,
        agent_skills_min_score=0.0,
    )
    service = SkillService(settings, source=NoIoSource())  # type: ignore[arg-type]
    source_revision = "a" * 40
    service._activate(
        SourceSnapshot(
            root=tmp_path,
            revision=source_revision,
            status=SourceStatus.CACHED,
            refreshed_at=1.0,
            source_url="fixture://local",
            ref="fixture",
        )
    )

    from app.skills import documents

    original_read = documents._read_relative_regular_file
    reads: list[str] = []

    def observe_read(*args, **kwargs):
        reads.append(args[1])
        return original_read(*args, **kwargs)

    monkeypatch.setattr(documents, "_read_relative_regular_file", observe_read)

    selected = service.select("literature papers review", flow="deep_research")
    assert reads == []
    assert service.status()["loaded_count"] == 0

    rendered = service.render(selected.names, revision=selected.revision)

    assert selected.names == ("literature-review",)
    assert selected.revision == source_revision
    assert "PINNED LOCAL GUIDANCE" in rendered
    assert reads == [skill_path.relative_to(tmp_path).as_posix()]
    assert service.status()["loaded_count"] == 1
    assert service.status()["cache_misses"] == 1

    assert "PINNED LOCAL GUIDANCE" in service.render(
        selected.names,
        revision=selected.revision,
    )
    assert len(reads) == 1
    assert service.status()["cache_hits"] == 1
    assert (
        service.render(
            selected.names,
            revision=selected.revision,
            max_chars=500,
        )
        == ""
    )


def test_render_fails_closed_after_selected_body_is_tampered(
    tmp_path: Path,
    write_skill,
) -> None:
    skill_path = write_skill(
        tmp_path,
        "review",
        name="tamper-review",
        description="Review evidence for papers",
        body="ORIGINAL\n",
    )
    service = SkillService(
        Settings(
            agent_skills_enabled=True,
            agent_skills_cache_dir=str(tmp_path / "cache"),
            agent_skills_min_score=0.0,
        ),
        source=NoIoSource(),  # type: ignore[arg-type]
    )
    service._activate(
        SourceSnapshot(
            root=tmp_path,
            revision="c" * 40,
            status=SourceStatus.CACHED,
            refreshed_at=1.0,
            source_url="fixture://local",
            ref="fixture",
        )
    )
    selected = service.select("review papers evidence", flow="deep_research")
    skill_path.write_text(skill_path.read_text() + "TAMPERED\n", encoding="utf-8")

    assert service.render(selected.names, revision=selected.revision) == ""
    assert service.status()["loaded_count"] == 0
    assert service.status()["cache_misses"] == 1


def test_preview_and_status_views_remain_revision_atomic_during_activation(
    tmp_path: Path,
    write_skill,
    monkeypatch,
) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    write_skill(root_a, "alpha", name="alpha-skill", tags=("Alpha Skill",))
    write_skill(
        root_a,
        "0-autoresearch-skill",
        name="autoresearch",
        tags=("Autoresearch",),
    )
    write_skill(root_b, "beta", name="beta-skill", tags=("Beta Skill",))
    write_skill(root_b, "gamma", name="gamma-skill", tags=("Gamma Skill",))

    service = SkillService(
        Settings(
            agent_skills_enabled=True,
            agent_skills_cache_dir=str(tmp_path / "cache"),
            agent_skills_min_score=0.0,
        ),
        source=NoIoSource(),  # type: ignore[arg-type]
    )
    snapshot_a = SourceSnapshot(
        root=root_a,
        revision="a" * 40,
        status=SourceStatus.CACHED,
        refreshed_at=1.0,
        source_url="fixture://a",
        ref="fixture",
    )
    snapshot_b = SourceSnapshot(
        root=root_b,
        revision="b" * 40,
        status=SourceStatus.CACHED,
        refreshed_at=2.0,
        source_url="fixture://b",
        ref="fixture",
    )
    service._activate(snapshot_a)
    registry_a = service._registries[snapshot_a.revision]

    original_select = registry_a.select

    def select_then_activate(*args, **kwargs):
        selected = original_select(*args, **kwargs)
        service._activate(snapshot_b)
        return selected

    monkeypatch.setattr(registry_a, "select", select_then_activate)
    preview, preview_status, loaded = service.preview_view(
        "alpha-skill",
        flow="deep_research",
    )

    assert [item.skill.name for item in preview.selections] == ["alpha-skill"]
    assert preview.source_revision == snapshot_a.revision
    assert preview_status["revision"] == snapshot_a.revision
    assert preview_status["available_count"] == 1
    assert preview_status["blocked_count"] == 1
    assert loaded == (False,)
    assert service.status()["revision"] == snapshot_b.revision

    service._activate(snapshot_a)
    original_cache_status = registry_a.cache_status

    def cache_status_then_activate():
        service._activate(snapshot_b)
        return original_cache_status()

    monkeypatch.setattr(registry_a, "cache_status", cache_status_then_activate)
    status, diagnostics = service.status_view()

    assert status["revision"] == snapshot_a.revision
    assert status["available_count"] == 1
    assert status["blocked_count"] == 1
    assert [item["code"] for item in diagnostics] == ["policy_blocked"]
    assert service._current_revision == snapshot_b.revision


def test_metadata_serializer_never_exposes_a_skill_body(
    tmp_path: Path,
    write_skill,
) -> None:
    from app.api.skills import _serialize_skill
    from app.skills.registry import load_skill_catalog

    body_secret = "BODY-MUST-NOT-BE-IN-METADATA"
    write_skill(
        tmp_path,
        "private-body",
        name="metadata-only",
        description="Public catalog description",
        body=f"{body_secret}\n",
    )
    descriptor = load_skill_catalog(tmp_path).require("metadata-only")

    payload = _serialize_skill(descriptor)

    assert "body" not in payload
    assert body_secret not in repr(payload)
    assert payload["name"] == "metadata-only"
    assert payload["description"] == "Public catalog description"


def test_paper_qa_requires_a_writing_action_before_selecting_writing_skills(
    tmp_path: Path,
    write_skill,
) -> None:
    write_skill(
        tmp_path,
        "20-ml-paper-writing/ml-paper-writing",
        name="ml-paper-writing",
        description="论文 method writing guidance",
        category="ml-paper-writing",
        tags=("论文", "method"),
    )
    settings = Settings(
        agent_skills_enabled=True,
        agent_skills_cache_dir=str(tmp_path / "cache"),
        agent_skills_min_score=0.0,
    )
    service = SkillService(settings, source=NoIoSource())  # type: ignore[arg-type]
    service._activate(
        SourceSnapshot(
            root=tmp_path,
            revision="b" * 40,
            status=SourceStatus.CACHED,
            refreshed_at=1.0,
            source_url="fixture://local",
            ref="fixture",
        )
    )

    understanding = service.select("这篇论文的方法是什么？", flow="paper_qa")
    writing = service.select("帮我写这篇论文的方法部分", flow="paper_qa")

    assert understanding.names == ()
    assert writing.names == ("ml-paper-writing",)


def test_disabled_loader_ignores_invalid_loader_configuration() -> None:
    settings = Settings(
        agent_skills_enabled=False,
        agent_skills_repo_url="https://evil.example/repo.git",
        agent_skills_repo_ref="../unsafe",
        agent_skills_refresh_seconds=-1,
        agent_skills_clone_timeout_seconds=-1,
        agent_skills_max_count=-1,
        agent_skills_max_file_bytes=-1,
        agent_skills_max_selected=-1,
        agent_skills_max_prompt_chars=1,
        agent_skills_min_score=-1,
        agent_skills_cache_max_entries=-1,
        agent_skills_cache_max_bytes=-1,
        agent_skills_max_reference_bytes=-1,
    )

    service = SkillService(settings)

    assert service.source is None
    assert service.status()["state"] == "disabled"
    assert service.select("anything", flow="paper_qa").names == ()


def test_enabled_loader_rejects_unsafe_prompt_budget() -> None:
    with pytest.raises(ValidationError, match="max_prompt_chars"):
        Settings(agent_skills_enabled=True, agent_skills_max_prompt_chars=500)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("agent_skills_max_count", 2_049),
        ("agent_skills_max_file_bytes", 2 * 1024 * 1024 + 1),
        ("agent_skills_max_selected", 9),
        ("agent_skills_cache_max_entries", 513),
        ("agent_skills_cache_max_bytes", 1_023),
        ("agent_skills_cache_max_bytes", 64 * 1024 * 1024 + 1),
        ("agent_skills_max_reference_bytes", 2 * 1024 * 1024 + 1),
    ),
)
def test_enabled_loader_rejects_unsafe_cache_and_document_bounds(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        Settings(agent_skills_enabled=True, **{field: value})
