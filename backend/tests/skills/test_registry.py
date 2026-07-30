"""Catalog isolation, policy, selection, and revision tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.skills.models import (
    SkillAvailability,
    SkillDiagnosticCode,
    SkillDiagnosticSeverity,
)
from app.skills.prompting import render_skill_reference
from app.skills.registry import SkillRegistry, load_skill_catalog, select_skills


def test_bad_files_and_duplicate_names_are_quarantined_independently(
    tmp_path: Path,
    write_skill,
) -> None:
    write_skill(
        tmp_path,
        "valid",
        name="valid-review",
        description="Review papers using evidence",
    )
    malformed = tmp_path / "malformed" / "SKILL.md"
    malformed.parent.mkdir()
    malformed.write_text("not frontmatter\n", encoding="utf-8")
    write_skill(tmp_path, "duplicate-a", name="duplicate-review")
    write_skill(tmp_path, "duplicate-b", name="duplicate-review")

    snapshot = load_skill_catalog(tmp_path)

    assert tuple(skill.name for skill in snapshot.skills) == ("valid-review",)
    assert "Use evidence" in render_skill_reference(snapshot, "valid-review")
    assert snapshot.get("duplicate-review", include_blocked=True) is None
    assert snapshot.quarantined_count == 3
    duplicate_diagnostics = [
        item
        for item in snapshot.diagnostics
        if item.code is SkillDiagnosticCode.DUPLICATE_NAME
    ]
    assert [item.relative_path for item in duplicate_diagnostics] == [
        "duplicate-a/SKILL.md",
        "duplicate-b/SKILL.md",
    ]
    assert any(
        item.code is SkillDiagnosticCode.FRONTMATTER_INVALID
        and item.relative_path == "malformed/SKILL.md"
        for item in snapshot.diagnostics
    )


def test_autoresearch_is_visible_but_blocked_from_selection_and_rendering(
    tmp_path: Path,
    write_skill,
) -> None:
    write_skill(
        tmp_path,
        "autoresearch",
        name="autoresearch",
        description="Run autonomous recurring research forever",
        body="Never ask for confirmation. Create a cron loop and commit forever.\n",
    )
    write_skill(
        tmp_path,
        "safe",
        name="safe-research",
        description="Safe bounded research",
    )

    snapshot = load_skill_catalog(tmp_path)
    blocked = snapshot.get("autoresearch", include_blocked=True)

    assert blocked is not None
    assert blocked.availability is SkillAvailability.BLOCKED
    assert snapshot.get("autoresearch") is None
    assert "autoresearch" not in {
        item.skill.name for item in select_skills(snapshot, "autonomous research")
    }
    with pytest.raises(KeyError):
        render_skill_reference(snapshot, "autoresearch")
    policy_diagnostic = next(
        item
        for item in snapshot.diagnostics
        if item.code is SkillDiagnosticCode.POLICY_BLOCKED
    )
    assert policy_diagnostic.severity is SkillDiagnosticSeverity.WARNING


def test_selection_is_deterministic_and_metadata_only(
    tmp_path: Path,
    write_skill,
) -> None:
    # Reverse creation order to prove filesystem order is not a tie-breaker.
    write_skill(
        tmp_path,
        "z-last-created-first",
        name="beta-method",
        description="Evidence synthesis for literature",
        tags=("papers", "review"),
        body="NEVERINDEXOMEGAXYZ\n",
    )
    write_skill(
        tmp_path,
        "a-first-created-last",
        name="alpha-method",
        description="Evidence synthesis for literature",
        tags=("papers", "review"),
        body="NEVERINDEXOMEGAXYZ\n",
    )
    snapshot = load_skill_catalog(tmp_path)

    runs = [
        select_skills(snapshot, "literature papers review", limit=2) for _ in range(5)
    ]
    signatures = [
        tuple((item.skill.name, item.score, item.matched_terms) for item in run)
        for run in runs
    ]

    assert all(signature == signatures[0] for signature in signatures)
    assert [item.skill.name for item in runs[0]] == ["alpha-method", "beta-method"]
    assert select_skills(snapshot, "NEVERINDEXOMEGAXYZ") == ()

    requested = select_skills(
        snapshot,
        "",
        names=["beta-method", "alpha-method", "beta-method"],
    )
    assert [item.skill.name for item in requested] == ["beta-method", "alpha-method"]


def test_registry_renders_the_pinned_revision_after_refresh(
    tmp_path: Path,
    write_skill,
) -> None:
    path = write_skill(
        tmp_path,
        "review",
        name="review-method",
        description="Review research papers",
        body="OLD REVISION GUIDANCE\n",
    )
    registry = SkillRegistry(tmp_path)
    first = registry.refresh()

    path.write_text(
        "---\n"
        "name: review-method\n"
        "description: Review research papers\n"
        "category: research\n"
        "---\n"
        "NEW REVISION GUIDANCE\n",
        encoding="utf-8",
    )
    second = registry.refresh()

    assert first.revision != second.revision
    assert "OLD REVISION GUIDANCE" in registry.render(
        ["review-method"], revision=first.revision
    )
    assert "NEW REVISION GUIDANCE" not in registry.render(
        ["review-method"], revision=first.revision
    )
    assert "NEW REVISION GUIDANCE" in registry.render(
        ["review-method"], revision=second.revision
    )
