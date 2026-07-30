"""Fixture-based validation tests for untrusted SKILL.md parsing."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from app.skills.models import DEFAULT_CATALOG_LIMITS, SkillDiagnosticCode
from app.skills.parser import SkillFileError, parse_skill_bytes, parse_skill_file


def test_crlf_document_is_normalized_without_changing_source_identity() -> None:
    data = (
        b"---\r\n"
        b"name: literature-review\r\n"
        b"description: Review a literature corpus\r\n"
        b"tags: [papers, synthesis]\r\n"
        b"---\r\n"
        b"Compare the evidence.\r\nPreserve caveats.\r\n"
    )

    parsed = parse_skill_bytes(data, relative_path="review/SKILL.md")

    assert parsed.descriptor.name == "literature-review"
    assert parsed.descriptor.metadata.tags == ("papers", "synthesis")
    assert parsed.body == "Compare the evidence.\nPreserve caveats.\n"
    assert "\r" not in parsed.body
    assert parsed.descriptor.body_chars == len(parsed.body)
    assert parsed.descriptor.byte_size == len(data)
    assert parsed.descriptor.content_sha256 == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "frontmatter",
    [
        "name: safe-skill\nName: duplicate-name\ndescription: duplicate key",
        "name: safe-skill\ndescription: &shared anchored text",
        "name: safe-skill\ndescription: *shared",
    ],
    ids=["duplicate-normalized-key", "yaml-anchor", "yaml-alias"],
)
def test_ambiguous_yaml_features_are_rejected(frontmatter: str) -> None:
    data = f"---\n{frontmatter}\n---\nBody.\n".encode()

    with pytest.raises(SkillFileError) as raised:
        parse_skill_bytes(data, relative_path="unsafe/SKILL.md")

    assert raised.value.code is SkillDiagnosticCode.FRONTMATTER_INVALID


@pytest.mark.parametrize(
    "data",
    [
        b"---\nname: bad\ndescription: bad encoding\n---\n\xff\n",
        b"---\nname: bad\ndescription: NUL\n---\nbody\x00\n",
    ],
    ids=["invalid-utf8", "nul-byte"],
)
def test_invalid_text_encoding_is_rejected(data: bytes) -> None:
    with pytest.raises(SkillFileError) as raised:
        parse_skill_bytes(data, relative_path="bad/SKILL.md")

    assert raised.value.code is SkillDiagnosticCode.UTF8_INVALID


def test_file_size_limit_is_enforced_before_parsing() -> None:
    data = b"---\nname: too-large\ndescription: oversized\n---\n" + (b"x" * 80)
    limits = replace(DEFAULT_CATALOG_LIMITS, max_file_bytes=len(data) - 1)

    with pytest.raises(SkillFileError) as raised:
        parse_skill_bytes(data, relative_path="large/SKILL.md", limits=limits)

    assert raised.value.code is SkillDiagnosticCode.FILE_TOO_LARGE


def test_parser_rejects_path_outside_catalog_root(
    tmp_path: Path,
    write_skill,
) -> None:
    root = tmp_path / "catalog"
    root.mkdir()
    outside = write_skill(tmp_path, "outside", name="outside-skill")

    with pytest.raises(SkillFileError) as raised:
        parse_skill_file(outside, root=root)

    assert raised.value.code is SkillDiagnosticCode.PATH_INVALID


def test_parser_rejects_symlinked_skill(
    tmp_path: Path,
    write_skill,
) -> None:
    root = tmp_path / "catalog"
    target = write_skill(root, "target", name="target-skill")
    link = root / "linked" / "SKILL.md"
    link.parent.mkdir()
    try:
        link.symlink_to(target)
    except OSError as exc:  # pragma: no cover - platform capability guard
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(SkillFileError) as raised:
        parse_skill_file(link, root=root)

    assert raised.value.code is SkillDiagnosticCode.SYMLINK_REJECTED
