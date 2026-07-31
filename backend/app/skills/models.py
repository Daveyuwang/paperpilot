"""Immutable domain models for the advisory skill catalog.

Skill files are third-party data.  The models in this module deliberately keep
catalog metadata separate from prompt placement and from executable behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType


class SkillDiagnosticCode(str, Enum):
    ROOT_INVALID = "root_invalid"
    COUNT_LIMIT_EXCEEDED = "count_limit_exceeded"
    PATH_INVALID = "path_invalid"
    SYMLINK_REJECTED = "symlink_rejected"
    NOT_A_REGULAR_FILE = "not_a_regular_file"
    FILE_TOO_LARGE = "file_too_large"
    UTF8_INVALID = "utf8_invalid"
    FRONTMATTER_INVALID = "frontmatter_invalid"
    METADATA_INVALID = "metadata_invalid"
    DUPLICATE_NAME = "duplicate_name"
    POLICY_BLOCKED = "policy_blocked"


class SkillDiagnosticSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class SkillAvailability(str, Enum):
    AVAILABLE = "available"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SkillCatalogLimits:
    """Hard resource and metadata limits applied while loading a catalog."""

    max_skill_files: int = 512
    max_file_bytes: int = 512 * 1024
    max_frontmatter_bytes: int = 32 * 1024
    max_path_chars: int = 1024
    max_path_depth: int = 16
    max_name_chars: int = 96
    max_description_chars: int = 4096
    max_tags: int = 48
    max_tag_chars: int = 96
    max_dependencies: int = 64
    max_dependency_chars: int = 160

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")


@dataclass(frozen=True)
class SkillRenderLimits:
    """Bounds for lazily rendering selected skill bodies into user data."""

    max_skills: int = 4
    max_chars_per_skill: int = 12_000
    max_chars_total: int = 24_000

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")


DEFAULT_CATALOG_LIMITS = SkillCatalogLimits()
DEFAULT_RENDER_LIMITS = SkillRenderLimits()


@dataclass(frozen=True)
class SkillDiagnostic:
    code: SkillDiagnosticCode
    message: str
    relative_path: str
    severity: SkillDiagnosticSeverity = SkillDiagnosticSeverity.ERROR


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    tags: tuple[str, ...] = ()
    category: str = ""
    version: str = ""
    author: str = ""
    license: str = ""
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillDescriptor:
    """Public, body-free description of one validated ``SKILL.md``."""

    metadata: SkillMetadata
    relative_path: str
    content_sha256: str
    byte_size: int
    body_chars: int
    references: tuple[SkillReferenceDescriptor, ...] = ()
    availability: SkillAvailability = SkillAvailability.AVAILABLE
    blocked_reason: str = ""

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def is_available(self) -> bool:
        return self.availability is SkillAvailability.AVAILABLE

    @property
    def reference_paths(self) -> tuple[str, ...]:
        return tuple(reference.relative_path for reference in self.references)


@dataclass(frozen=True)
class SkillReferenceDescriptor:
    """Manifest-pinned metadata for one optional Markdown reference."""

    relative_path: str
    content_sha256: str


@dataclass(frozen=True)
class SkillSelection:
    skill: SkillDescriptor
    score: float
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderedSkillReference:
    """Bounded untrusted text intended only for a user/data message."""

    content: str
    revision: str
    skill_names: tuple[str, ...]
    truncated_skill_names: tuple[str, ...] = ()

    def __str__(self) -> str:
        return self.content


@dataclass(frozen=True)
class SkillCatalogSnapshot:
    """An immutable, metadata-only view of one catalog revision.

    Skill and reference text is deliberately absent.  Runtime callers resolve
    selected documents lazily from ``root`` and verify them against the pinned
    descriptors before use.
    """

    root: Path
    root_device: int
    root_inode: int
    revision: str
    skills: tuple[SkillDescriptor, ...]
    diagnostics: tuple[SkillDiagnostic, ...]
    loaded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _by_name: Mapping[str, SkillDescriptor] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        ordered = tuple(
            sorted(self.skills, key=lambda item: (item.name, item.relative_path))
        )
        if ordered != self.skills:
            object.__setattr__(self, "skills", ordered)

        by_name = {skill.name: skill for skill in ordered}
        if len(by_name) != len(ordered):
            raise ValueError("snapshot skills must have unique names")

        object.__setattr__(self, "_by_name", MappingProxyType(by_name))

    @property
    def available_skills(self) -> tuple[SkillDescriptor, ...]:
        return tuple(skill for skill in self.skills if skill.is_available)

    @property
    def blocked_skills(self) -> tuple[SkillDescriptor, ...]:
        return tuple(skill for skill in self.skills if not skill.is_available)

    @property
    def quarantined_count(self) -> int:
        return sum(
            diagnostic.severity is SkillDiagnosticSeverity.ERROR
            for diagnostic in self.diagnostics
        )

    def get(
        self, name: str, *, include_blocked: bool = False
    ) -> SkillDescriptor | None:
        skill = self._by_name.get(name)
        if skill is None or (not include_blocked and not skill.is_available):
            return None
        return skill

    def require(self, name: str, *, include_blocked: bool = False) -> SkillDescriptor:
        skill = self.get(name, include_blocked=include_blocked)
        if skill is None:
            raise KeyError(
                f"skill is not available in revision {self.revision}: {name}"
            )
        return skill


@dataclass(frozen=True)
class ParsedSkillFile:
    """Validated parse result used transiently while building a snapshot."""

    descriptor: SkillDescriptor
    body: str = field(repr=False)
