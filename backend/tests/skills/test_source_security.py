"""Focused cleanup regressions for private source staging trees."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from app.skills.source import _remove_tree


def test_staging_cleanup_unlinks_symlink_without_chmodding_external_target(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("must survive cleanup\n", encoding="utf-8")
    outside.chmod(0o440)
    original_mode = stat.S_IMODE(outside.stat().st_mode)

    staging = tmp_path / "private-stage"
    staging.mkdir()
    link = staging / "upstream-link"
    try:
        link.symlink_to(outside)
    except OSError as exc:  # pragma: no cover - platform capability guard
        pytest.skip(f"symlinks unavailable: {exc}")

    _remove_tree(staging)

    assert not staging.exists()
    assert outside.read_text(encoding="utf-8") == "must survive cleanup\n"
    assert stat.S_IMODE(outside.stat().st_mode) == original_mode
