"""Request-path tests for the in-memory skill service facade."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.skills.service import SkillService
from app.skills.source import SourceSnapshot, SourceStatus
from pydantic import ValidationError


class NoIoSource:
    """A source double that fails if request-time code consults the source."""

    def load_current(self):
        raise AssertionError("select/render must not load source state")

    def get_snapshot(self, **_kwargs):
        raise AssertionError("select/render must not fetch source state")


def test_select_and_render_use_only_the_activated_in_memory_snapshot(
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

    # The request path must continue to work even when the activated source
    # document is gone and every parser/catalog entry point would explode.
    skill_path.unlink()

    def unexpected_io(*_args, **_kwargs):
        raise AssertionError("select/render attempted parser or catalog I/O")

    monkeypatch.setattr("app.skills.parser.parse_skill_file", unexpected_io)
    monkeypatch.setattr("app.skills.registry.load_skill_catalog", unexpected_io)

    selected = service.select("literature papers review", flow="deep_research")
    rendered = service.render(selected.names, revision=selected.revision)

    assert selected.names == ("literature-review",)
    assert selected.revision == source_revision
    assert "PINNED LOCAL GUIDANCE" in rendered
    assert (
        service.render(
            selected.names,
            revision=selected.revision,
            max_chars=500,
        )
        == ""
    )


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
    )

    service = SkillService(settings)

    assert service.source is None
    assert service.status()["state"] == "disabled"
    assert service.select("anything", flow="paper_qa").names == ()


def test_enabled_loader_rejects_unsafe_prompt_budget() -> None:
    with pytest.raises(ValidationError, match="max_prompt_chars"):
        Settings(agent_skills_enabled=True, agent_skills_max_prompt_chars=500)
