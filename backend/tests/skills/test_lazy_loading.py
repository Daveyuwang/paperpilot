"""Progressive disclosure, cache, revision, and document-security tests."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.skills.documents import LazyDocumentCache
from app.skills.service import SelectedSkillContext, SkillService
from app.skills.source import SourceSnapshot, SourceStatus


class OfflineSource:
    def load_current(self):
        raise AssertionError("request path attempted source I/O")

    def get_snapshot(self, **_kwargs):
        raise AssertionError("request path attempted network refresh")


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "agent_skills_enabled": True,
        "agent_skills_cache_dir": str(tmp_path / "cache"),
        "agent_skills_min_score": 0.0,
        "agent_skills_max_selected": 3,
        "agent_skills_max_prompt_chars": 4_000,
        "agent_skills_cache_max_entries": 4,
        "agent_skills_cache_max_bytes": 20_000,
    }
    values.update(overrides)
    return Settings(**values)


def _activate(service: SkillService, root: Path, revision: str) -> None:
    service._activate(
        SourceSnapshot(
            root=root,
            revision=revision,
            status=SourceStatus.CACHED,
            refreshed_at=1.0,
            source_url="fixture://local",
            ref="fixture",
        )
    )


def test_global_lru_obeys_entry_and_byte_bounds() -> None:
    cache = LazyDocumentCache(max_entries=2, max_bytes=6)
    loads: list[str] = []

    def load(value: str):
        return lambda: loads.append(value) or value

    key_a = ("a" * 40, "catalog", "a/SKILL.md", "1" * 64)
    key_b = ("a" * 40, "catalog", "b/SKILL.md", "2" * 64)
    key_c = ("b" * 40, "catalog-2", "c/SKILL.md", "3" * 64)
    cache.get_or_load(key_a, kind="skill", skill_name="a", loader=load("aaa"))
    cache.get_or_load(key_b, kind="skill", skill_name="b", loader=load("bbb"))
    cache.get_or_load(key_a, kind="skill", skill_name="a", loader=load("unused"))
    cache.get_or_load(key_c, kind="skill", skill_name="c", loader=load("ccc"))

    status = cache.status()
    assert loads == ["aaa", "bbb", "ccc"]
    assert cache.contains(key_a)
    assert not cache.contains(key_b)
    assert cache.contains(key_c)
    assert status["cache_entry_count"] == 2
    assert status["cache_total_bytes"] == 6
    assert status["cache_hits"] == 1
    assert status["cache_misses"] == 3
    assert status["cache_evictions"] == 1

    oversized = ("c" * 40, "catalog-3", "large/SKILL.md", "4" * 64)
    assert (
        cache.get_or_load(
            oversized,
            kind="skill",
            skill_name="large",
            loader=load("1234567"),
        )
        == "1234567"
    )
    assert not cache.contains(oversized)
    assert cache.status()["cache_total_bytes"] <= cache.max_bytes


def test_retained_registry_lazily_reads_its_pinned_source_revision(
    tmp_path: Path,
    write_skill,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    write_skill(
        first_root,
        "review",
        name="revision-review",
        description="Review papers with evidence",
        body="FIRST PINNED BODY\n",
    )
    write_skill(
        second_root,
        "review",
        name="revision-review",
        description="Review papers with evidence",
        body="SECOND PINNED BODY\n",
    )
    service = SkillService(
        _settings(tmp_path),
        source=OfflineSource(),  # type: ignore[arg-type]
    )

    first_revision = "1" * 40
    second_revision = "2" * 40
    _activate(service, first_root, first_revision)
    first_context = service.select("review papers evidence", flow="deep_research")
    _activate(service, second_root, second_revision)
    second_context = service.select("review papers evidence", flow="deep_research")

    first_render = service.render(
        first_context.names,
        revision=first_context.revision,
    )
    second_render = service.render(
        second_context.names,
        revision=second_context.revision,
    )

    assert "FIRST PINNED BODY" in first_render
    assert "SECOND PINNED BODY" not in first_render
    assert "SECOND PINNED BODY" in second_render
    assert service.status()["loaded_count"] == 1
    assert service.status()["cache_entry_count"] == 2


def test_render_loads_only_selected_bodies(
    tmp_path: Path,
    write_skill,
    monkeypatch,
) -> None:
    write_skill(
        tmp_path,
        "alpha",
        name="alpha-review",
        description="Alpha literature papers review",
        body="ALPHA BODY\n",
    )
    write_skill(
        tmp_path,
        "beta",
        name="beta-training",
        description="Beta distributed model training",
        body="BETA BODY\n",
    )
    service = SkillService(
        _settings(tmp_path, agent_skills_max_selected=1),
        source=OfflineSource(),  # type: ignore[arg-type]
    )
    _activate(service, tmp_path, "3" * 40)

    from app.skills import registry as registry_module

    original = registry_module.load_verified_skill_body
    loaded: list[str] = []

    def observe(root, descriptor, **kwargs):
        loaded.append(descriptor.name)
        return original(root, descriptor, **kwargs)

    monkeypatch.setattr(registry_module, "load_verified_skill_body", observe)
    context = service.select("alpha literature papers review", flow="deep_research")
    rendered = service.render(context.names, revision=context.revision)

    assert context.names == ("alpha-review",)
    assert loaded == ["alpha-review"]
    assert "ALPHA BODY" in rendered
    assert "BETA BODY" not in rendered


def test_root_replacement_and_symlinked_body_fail_closed(
    tmp_path: Path,
    write_skill,
) -> None:
    root = tmp_path / "snapshot"
    skill_path = write_skill(
        root,
        "secure",
        name="secure-review",
        description="Secure evidence review",
        body="SAFE BODY\n",
    )
    service = SkillService(
        _settings(tmp_path),
        source=OfflineSource(),  # type: ignore[arg-type]
    )
    _activate(service, root, "4" * 40)
    context = service.select("secure evidence review", flow="deep_research")

    moved = tmp_path / "moved-snapshot"
    root.rename(moved)
    root.mkdir()
    (root / "secure").mkdir()
    (root / "secure" / "SKILL.md").write_bytes(
        (moved / "secure" / "SKILL.md").read_bytes()
    )
    assert service.render(context.names, revision=context.revision) == ""

    second_root = tmp_path / "symlink-snapshot"
    second_path = write_skill(
        second_root,
        "secure",
        name="secure-review",
        description="Secure evidence review",
        body="SAFE BODY\n",
    )
    second_service = SkillService(
        _settings(tmp_path),
        source=OfflineSource(),  # type: ignore[arg-type]
    )
    _activate(second_service, second_root, "5" * 40)
    second_context = second_service.select(
        "secure evidence review", flow="deep_research"
    )
    outside = tmp_path / "outside.md"
    outside.write_text(skill_path.read_text(encoding="utf-8"), encoding="utf-8")
    second_path.unlink()
    second_path.symlink_to(outside)
    assert (
        second_service.render(
            second_context.names,
            revision=second_context.revision,
        )
        == ""
    )


def test_only_manifest_listed_owned_references_load_for_selected_skill(
    tmp_path: Path,
    write_skill,
    write_snapshot_manifest,
) -> None:
    write_skill(
        tmp_path,
        "review",
        name="reference-review",
        description="Review papers using a reference guide",
    )
    references = tmp_path / "review" / "references"
    references.mkdir()
    guide = references / "guide.md"
    guide.write_text("MANIFEST GUIDE\n", encoding="utf-8")
    write_snapshot_manifest(tmp_path)
    hidden = references / "hidden.md"
    hidden.write_text("NOT MANIFESTED\n", encoding="utf-8")

    service = SkillService(
        _settings(tmp_path),
        source=OfflineSource(),  # type: ignore[arg-type]
    )
    _activate(service, tmp_path, "6" * 40)
    context = service.select("review papers reference guide", flow="deep_research")
    descriptor = service.descriptor("reference-review")

    assert descriptor is not None
    assert descriptor.reference_paths == ("review/references/guide.md",)
    loaded = service.load_references(
        context,
        "reference-review",
        ["references/guide.md"],
    )
    assert tuple(item.content for item in loaded) == ("MANIFEST GUIDE\n",)
    assert service.status()["loaded_reference_count"] == 1

    assert (
        service.load_references(
            context,
            "reference-review",
            ["references/hidden.md"],
        )
        == ()
    )
    assert (
        service.load_references(
            context,
            "reference-review",
            ["../outside.md"],
        )
        == ()
    )
    unselected = SelectedSkillContext(
        names=("another-skill",),
        revision=context.revision,
    )
    assert (
        service.load_references(
            unselected,
            "reference-review",
            ["references/guide.md"],
        )
        == ()
    )


def test_unknown_and_blocked_names_never_trigger_document_reads(
    tmp_path: Path,
    write_skill,
    monkeypatch,
) -> None:
    write_skill(
        tmp_path,
        "autoresearch",
        name="autoresearch",
        description="Autonomous research loop",
    )
    write_skill(
        tmp_path,
        "safe",
        name="safe-research",
        description="Safe bounded research",
    )
    service = SkillService(
        _settings(tmp_path),
        source=OfflineSource(),  # type: ignore[arg-type]
    )
    revision = "7" * 40
    _activate(service, tmp_path, revision)

    from app.skills import documents

    monkeypatch.setattr(
        documents,
        "_read_relative_regular_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("blocked/unknown name caused a read")
        ),
    )
    assert service.render(["autoresearch"], revision=revision) == ""
    assert service.render(["missing"], revision=revision) == ""
    assert service.status()["cache_misses"] == 0
