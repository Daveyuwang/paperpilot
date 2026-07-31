"""Advisory skill catalog public API.

The loader reads metadata and Markdown only.  It never imports dependencies,
executes scripts, or promotes third-party content to a system instruction.
"""

from .models import (
    DEFAULT_CATALOG_LIMITS,
    DEFAULT_RENDER_LIMITS,
    ParsedSkillFile,
    RenderedSkillReference,
    SkillAvailability,
    SkillCatalogLimits,
    SkillCatalogSnapshot,
    SkillDescriptor,
    SkillDiagnostic,
    SkillDiagnosticCode,
    SkillDiagnosticSeverity,
    SkillMetadata,
    SkillRenderLimits,
    SkillSelection,
)
from .parser import (
    SkillFileError,
    normalize_newlines,
    parse_skill_bytes,
    parse_skill_file,
)
from .prompting import (
    SKILL_POLICY_SUFFIX,
    attach_skill_reference,
    render_skill_reference,
    render_skill_references,
    with_skill_policy,
)
from .registry import (
    DEFAULT_BLOCKED_SKILL_NAMES,
    SkillRegistry,
    SkillSnapshotNotFoundError,
    load_skill_catalog,
    select_skills,
)

__all__ = [
    "DEFAULT_BLOCKED_SKILL_NAMES",
    "DEFAULT_CATALOG_LIMITS",
    "DEFAULT_RENDER_LIMITS",
    "SKILL_POLICY_SUFFIX",
    "ParsedSkillFile",
    "RenderedSkillReference",
    "SkillAvailability",
    "SkillCatalogLimits",
    "SkillCatalogSnapshot",
    "SkillDescriptor",
    "SkillDiagnostic",
    "SkillDiagnosticCode",
    "SkillDiagnosticSeverity",
    "SkillFileError",
    "SkillMetadata",
    "SkillRegistry",
    "SkillRenderLimits",
    "SkillSelection",
    "SkillSnapshotNotFoundError",
    "attach_skill_reference",
    "load_skill_catalog",
    "normalize_newlines",
    "parse_skill_bytes",
    "parse_skill_file",
    "render_skill_reference",
    "render_skill_references",
    "select_skills",
    "with_skill_policy",
]
