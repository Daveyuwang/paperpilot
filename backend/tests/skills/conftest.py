"""Shared, fully local fixtures for the advisory skill loader tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

SkillWriter = Callable[..., Path]


@pytest.fixture
def write_skill() -> SkillWriter:
    """Return a small SKILL.md writer with no dependency on an upstream clone."""

    def _write_skill(
        root: Path,
        directory: str,
        *,
        name: str,
        description: str = "Fixture research guidance",
        body: str = "Use evidence from the supplied papers.\n",
        tags: tuple[str, ...] = (),
        category: str = "research",
        newline: str = "\n",
        extra_frontmatter: tuple[str, ...] = (),
    ) -> Path:
        path = root / directory / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        header = [
            "---",
            f"name: {name}",
            f"description: {description}",
        ]
        if tags:
            header.append(f"tags: [{', '.join(tags)}]")
        if category:
            header.append(f"category: {category}")
        header.extend(extra_frontmatter)
        header.append("---")
        document = newline.join(header) + newline + body.replace("\n", newline)
        path.write_bytes(document.encode("utf-8"))
        return path

    return _write_skill
