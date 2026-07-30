"""Strict, side-effect-free parsing for third-party ``SKILL.md`` files."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from .models import (
    DEFAULT_CATALOG_LIMITS,
    ParsedSkillFile,
    SkillCatalogLimits,
    SkillDescriptor,
    SkillDiagnostic,
    SkillDiagnosticCode,
    SkillMetadata,
)

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NUMBERED_CATEGORY_RE = re.compile(r"^\d+[-_ ]*")
_CATEGORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")


class SkillFileError(ValueError):
    """A quarantinable validation error for one catalog entry."""

    def __init__(
        self,
        code: SkillDiagnosticCode,
        message: str,
        relative_path: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.relative_path = relative_path

    def as_diagnostic(self) -> SkillDiagnostic:
        return SkillDiagnostic(
            code=self.code,
            message=str(self),
            relative_path=self.relative_path,
        )


def normalize_newlines(text: str) -> str:
    """Normalize CRLF and bare CR without otherwise altering source text."""

    return text.replace("\r\n", "\n").replace("\r", "\n")


def _validate_relative_label(
    relative_path: str,
    limits: SkillCatalogLimits,
) -> str:
    parts = relative_path.split("/")
    if (
        not relative_path
        or relative_path.startswith("/")
        or "\\" in relative_path
        or len(relative_path) > limits.max_path_chars
        or len(parts) > limits.max_path_depth
        or parts[-1] != "SKILL.md"
        or any(part in {"", ".", ".."} or _CONTROL_RE.search(part) for part in parts)
    ):
        raise SkillFileError(
            SkillDiagnosticCode.PATH_INVALID,
            "skill path is invalid, unsafe, or exceeds catalog limits",
            relative_path or ".",
        )
    return relative_path


def _safe_relative_path(
    root: Path,
    path: Path,
    limits: SkillCatalogLimits,
) -> tuple[Path, str]:
    if root.is_symlink():
        raise SkillFileError(
            SkillDiagnosticCode.SYMLINK_REJECTED,
            "catalog root cannot be a symlink",
            ".",
        )
    try:
        root_resolved = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SkillFileError(
            SkillDiagnosticCode.ROOT_INVALID,
            f"catalog root cannot be resolved: {exc}",
            ".",
        ) from exc

    if not root_resolved.is_dir():
        raise SkillFileError(
            SkillDiagnosticCode.ROOT_INVALID,
            "catalog root is not a directory",
            ".",
        )

    candidate = path if path.is_absolute() else root_resolved / path
    if ".." in candidate.parts:
        raise SkillFileError(
            SkillDiagnosticCode.PATH_INVALID,
            "skill path cannot contain parent traversal",
            candidate.name or ".",
        )
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise SkillFileError(
            SkillDiagnosticCode.PATH_INVALID,
            "skill path escapes the configured catalog root",
            candidate.name or ".",
        ) from exc

    relative_path = _validate_relative_label(relative.as_posix(), limits)

    current = root_resolved
    for part in relative.parts:
        if part in {"", ".", ".."} or "\\" in part or _CONTROL_RE.search(part):
            raise SkillFileError(
                SkillDiagnosticCode.PATH_INVALID,
                "skill path contains an unsafe component",
                relative_path,
            )
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except OSError as exc:
            raise SkillFileError(
                SkillDiagnosticCode.PATH_INVALID,
                f"skill path cannot be inspected: {exc}",
                relative_path,
            ) from exc
        if stat.S_ISLNK(mode):
            raise SkillFileError(
                SkillDiagnosticCode.SYMLINK_REJECTED,
                "symlinks are not allowed in skill paths",
                relative_path,
            )

    return candidate, relative_path


def _read_regular_file(
    path: Path,
    *,
    relative_path: str,
    max_bytes: int,
) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        code = (
            SkillDiagnosticCode.SYMLINK_REJECTED
            if getattr(exc, "errno", None) == errno.ELOOP
            else SkillDiagnosticCode.PATH_INVALID
        )
        raise SkillFileError(
            code, f"skill file cannot be opened: {exc}", relative_path
        ) from exc

    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise SkillFileError(
                SkillDiagnosticCode.NOT_A_REGULAR_FILE,
                "skill entry is not a regular file",
                relative_path,
            )
        if file_stat.st_size > max_bytes:
            raise SkillFileError(
                SkillDiagnosticCode.FILE_TOO_LARGE,
                f"skill file exceeds {max_bytes} bytes",
                relative_path,
            )

        chunks: list[bytes] = []
        bytes_read = 0
        while bytes_read <= max_bytes:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - bytes_read))
            if not chunk:
                break
            chunks.append(chunk)
            bytes_read += len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise SkillFileError(
                SkillDiagnosticCode.FILE_TOO_LARGE,
                f"skill file exceeds {max_bytes} bytes",
                relative_path,
            )
        return data
    finally:
        os.close(descriptor)


def _strip_inline_comment(value: str) -> str:
    quote = ""
    escaped = False
    bracket_depth = 0
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth -= 1
        elif (
            char == "#"
            and bracket_depth == 0
            and (index == 0 or value[index - 1].isspace())
        ):
            return value[:index].rstrip()
    return value.rstrip()


def _split_inline_list(value: str, relative_path: str) -> list[Any]:
    inner = value[1:-1].strip()
    if not inner:
        return []

    items: list[str] = []
    start = 0
    quote = ""
    escaped = False
    for index, char in enumerate(inner):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in "[]{}":
            raise SkillFileError(
                SkillDiagnosticCode.FRONTMATTER_INVALID,
                "nested YAML structures are not permitted in skill metadata",
                relative_path,
            )
        elif char == ",":
            items.append(inner[start:index].strip())
            start = index + 1
    if quote:
        raise SkillFileError(
            SkillDiagnosticCode.FRONTMATTER_INVALID,
            "unterminated quote in YAML list",
            relative_path,
        )
    items.append(inner[start:].strip())
    if any(not item for item in items):
        raise SkillFileError(
            SkillDiagnosticCode.FRONTMATTER_INVALID,
            "empty YAML list item",
            relative_path,
        )
    return [_parse_scalar(item, relative_path) for item in items]


def _parse_scalar(value: str, relative_path: str) -> Any:
    value = _strip_inline_comment(value).strip()
    if not value:
        return ""
    if value.startswith("["):
        if not value.endswith("]"):
            raise SkillFileError(
                SkillDiagnosticCode.FRONTMATTER_INVALID,
                "unterminated YAML list",
                relative_path,
            )
        return _split_inline_list(value, relative_path)
    if value.startswith(("{", "&", "*", "!")):
        raise SkillFileError(
            SkillDiagnosticCode.FRONTMATTER_INVALID,
            "YAML mappings, aliases, anchors, and custom tags are not permitted",
            relative_path,
        )
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise SkillFileError(
                SkillDiagnosticCode.FRONTMATTER_INVALID,
                "invalid double-quoted YAML scalar",
                relative_path,
            ) from exc
        if not isinstance(parsed, str):
            raise SkillFileError(
                SkillDiagnosticCode.FRONTMATTER_INVALID,
                "quoted YAML metadata must be text",
                relative_path,
            )
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise SkillFileError(
                SkillDiagnosticCode.FRONTMATTER_INVALID,
                "invalid single-quoted YAML scalar",
                relative_path,
            )
        return value[1:-1].replace("''", "'")

    lowered = value.casefold()
    if lowered in {"null", "~"}:
        return None
    if lowered in {"true", "false"}:
        return lowered == "true"
    return value


def _parse_block_scalar(lines: list[str], marker: str) -> str:
    nonempty = [len(line) - len(line.lstrip(" ")) for line in lines if line.strip()]
    indentation = min(nonempty, default=0)
    values = [line[indentation:] if line.strip() else "" for line in lines]
    if marker.startswith("|"):
        result = "\n".join(values)
    else:
        paragraphs: list[str] = []
        current: list[str] = []
        for line in values:
            if line:
                current.append(line.strip())
            else:
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
                paragraphs.append("")
        if current:
            paragraphs.append(" ".join(current))
        result = "\n".join(paragraphs)
    if marker.endswith("-"):
        return result.rstrip("\n")
    return result


def _parse_frontmatter(header: str, relative_path: str) -> dict[str, Any]:
    lines = header.split("\n")
    parsed: dict[str, Any] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line.startswith((" ", "\t")) or "\t" in line:
            raise SkillFileError(
                SkillDiagnosticCode.FRONTMATTER_INVALID,
                f"unexpected YAML indentation on metadata line {index + 1}",
                relative_path,
            )
        if ":" not in line:
            raise SkillFileError(
                SkillDiagnosticCode.FRONTMATTER_INVALID,
                f"metadata line {index + 1} is not a key/value pair",
                relative_path,
            )
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not _KEY_RE.fullmatch(key):
            raise SkillFileError(
                SkillDiagnosticCode.FRONTMATTER_INVALID,
                f"invalid YAML metadata key on line {index + 1}",
                relative_path,
            )
        normalized_key = key.casefold().replace("-", "_")
        if normalized_key in parsed:
            raise SkillFileError(
                SkillDiagnosticCode.FRONTMATTER_INVALID,
                f"duplicate YAML metadata key: {key}",
                relative_path,
            )

        value = raw_value.strip()
        if value in {"|", "|-", "|+", ">", ">-", ">+"}:
            block_lines: list[str] = []
            index += 1
            while index < len(lines) and (
                not lines[index].strip() or lines[index].startswith(" ")
            ):
                if (
                    "\t"
                    in lines[index][: len(lines[index]) - len(lines[index].lstrip())]
                ):
                    raise SkillFileError(
                        SkillDiagnosticCode.FRONTMATTER_INVALID,
                        "tabs are not permitted for YAML indentation",
                        relative_path,
                    )
                block_lines.append(lines[index])
                index += 1
            parsed[normalized_key] = _parse_block_scalar(block_lines, value)
            continue

        if not value:
            list_items: list[Any] = []
            index += 1
            while index < len(lines) and (
                not lines[index].strip() or lines[index].startswith(" ")
            ):
                item_line = lines[index]
                index += 1
                if not item_line.strip():
                    continue
                if "\t" in item_line or not item_line.lstrip().startswith("- "):
                    raise SkillFileError(
                        SkillDiagnosticCode.FRONTMATTER_INVALID,
                        f"metadata field {key} must be a flat YAML list",
                        relative_path,
                    )
                list_items.append(_parse_scalar(item_line.lstrip()[2:], relative_path))
            parsed[normalized_key] = list_items
            continue

        parsed[normalized_key] = _parse_scalar(value, relative_path)
        index += 1
    return parsed


def _text_field(
    metadata: dict[str, Any],
    key: str,
    *,
    relative_path: str,
    required: bool = False,
    max_chars: int,
) -> str:
    value = metadata.get(key, "")
    if value is None:
        value = ""
    if isinstance(value, (list, dict, bool)):
        raise SkillFileError(
            SkillDiagnosticCode.METADATA_INVALID,
            f"metadata field {key} must be text",
            relative_path,
        )
    value = str(value).strip()
    if required and not value:
        raise SkillFileError(
            SkillDiagnosticCode.METADATA_INVALID,
            f"required metadata field is missing: {key}",
            relative_path,
        )
    if len(value) > max_chars or _CONTROL_RE.search(value):
        raise SkillFileError(
            SkillDiagnosticCode.METADATA_INVALID,
            f"metadata field {key} is invalid or exceeds {max_chars} characters",
            relative_path,
        )
    return value


def _text_list_field(
    metadata: dict[str, Any],
    key: str,
    *,
    relative_path: str,
    max_items: int,
    max_chars: int,
) -> tuple[str, ...]:
    value = metadata.get(key, [])
    if value in (None, ""):
        return ()
    values = value if isinstance(value, list) else [value]
    if len(values) > max_items:
        raise SkillFileError(
            SkillDiagnosticCode.METADATA_INVALID,
            f"metadata field {key} exceeds {max_items} items",
            relative_path,
        )
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise SkillFileError(
                SkillDiagnosticCode.METADATA_INVALID,
                f"metadata field {key} must contain text items",
                relative_path,
            )
        normalized = item.strip()
        canonical = normalized.casefold()
        if (
            not normalized
            or len(normalized) > max_chars
            or _CONTROL_RE.search(normalized)
        ):
            raise SkillFileError(
                SkillDiagnosticCode.METADATA_INVALID,
                f"metadata field {key} contains an invalid item",
                relative_path,
            )
        if canonical not in seen:
            result.append(normalized)
            seen.add(canonical)
    return tuple(result)


def _derived_category(relative_path: str) -> str:
    directory_parts = relative_path.split("/")[:-1]
    if len(directory_parts) < 2:
        return ""
    category = _NUMBERED_CATEGORY_RE.sub("", directory_parts[-2])
    return category.replace("_", "-").strip("- ")


def parse_skill_bytes(
    data: bytes,
    *,
    relative_path: str,
    limits: SkillCatalogLimits = DEFAULT_CATALOG_LIMITS,
) -> ParsedSkillFile:
    """Parse already-bounded file bytes without executing or importing anything."""

    relative_path = _validate_relative_label(relative_path, limits)
    if len(data) > limits.max_file_bytes:
        raise SkillFileError(
            SkillDiagnosticCode.FILE_TOO_LARGE,
            f"skill file exceeds {limits.max_file_bytes} bytes",
            relative_path,
        )
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SkillFileError(
            SkillDiagnosticCode.UTF8_INVALID,
            "skill file is not valid UTF-8",
            relative_path,
        ) from exc
    text = normalize_newlines(text)
    if "\x00" in text:
        raise SkillFileError(
            SkillDiagnosticCode.UTF8_INVALID,
            "skill file contains a NUL character",
            relative_path,
        )

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != "---":
        raise SkillFileError(
            SkillDiagnosticCode.FRONTMATTER_INVALID,
            "SKILL.md must begin with YAML frontmatter",
            relative_path,
        )

    closing_index: int | None = None
    header_bytes = 0
    for index in range(1, len(lines)):
        header_bytes += len(lines[index].encode("utf-8"))
        if header_bytes > limits.max_frontmatter_bytes:
            raise SkillFileError(
                SkillDiagnosticCode.FRONTMATTER_INVALID,
                f"YAML frontmatter exceeds {limits.max_frontmatter_bytes} bytes",
                relative_path,
            )
        if lines[index].rstrip("\n") == "---":
            closing_index = index
            break
    if closing_index is None:
        raise SkillFileError(
            SkillDiagnosticCode.FRONTMATTER_INVALID,
            "YAML frontmatter has no closing delimiter",
            relative_path,
        )

    header = "".join(lines[1:closing_index])
    body = "".join(lines[closing_index + 1 :]).lstrip("\n")
    raw_metadata = _parse_frontmatter(header, relative_path)

    name = _text_field(
        raw_metadata,
        "name",
        relative_path=relative_path,
        required=True,
        max_chars=limits.max_name_chars,
    )
    if not _NAME_RE.fullmatch(name):
        raise SkillFileError(
            SkillDiagnosticCode.METADATA_INVALID,
            "skill name must be lowercase kebab-case ASCII",
            relative_path,
        )
    description = _text_field(
        raw_metadata,
        "description",
        relative_path=relative_path,
        required=True,
        max_chars=limits.max_description_chars,
    )
    if not body.strip():
        raise SkillFileError(
            SkillDiagnosticCode.METADATA_INVALID,
            "skill body is empty",
            relative_path,
        )

    category = _text_field(
        raw_metadata,
        "category",
        relative_path=relative_path,
        max_chars=limits.max_tag_chars,
    ) or _derived_category(relative_path)
    if category and not _CATEGORY_RE.fullmatch(category):
        raise SkillFileError(
            SkillDiagnosticCode.METADATA_INVALID,
            "skill category contains unsupported characters",
            relative_path,
        )
    metadata = SkillMetadata(
        name=name,
        description=description,
        tags=_text_list_field(
            raw_metadata,
            "tags",
            relative_path=relative_path,
            max_items=limits.max_tags,
            max_chars=limits.max_tag_chars,
        ),
        category=category,
        version=_text_field(
            raw_metadata,
            "version",
            relative_path=relative_path,
            max_chars=64,
        ),
        author=_text_field(
            raw_metadata,
            "author",
            relative_path=relative_path,
            max_chars=160,
        ),
        license=_text_field(
            raw_metadata,
            "license",
            relative_path=relative_path,
            max_chars=96,
        ),
        dependencies=_text_list_field(
            raw_metadata,
            "dependencies",
            relative_path=relative_path,
            max_items=limits.max_dependencies,
            max_chars=limits.max_dependency_chars,
        ),
    )
    descriptor = SkillDescriptor(
        metadata=metadata,
        relative_path=relative_path,
        content_sha256=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
        body_chars=len(body),
    )
    return ParsedSkillFile(descriptor=descriptor, body=body)


def parse_skill_file(
    path: str | Path,
    *,
    root: str | Path,
    limits: SkillCatalogLimits = DEFAULT_CATALOG_LIMITS,
) -> ParsedSkillFile:
    """Validate and parse one in-root regular file using a no-follow open."""

    safe_path, relative_path = _safe_relative_path(Path(root), Path(path), limits)
    data = _read_regular_file(
        safe_path,
        relative_path=relative_path,
        max_bytes=limits.max_file_bytes,
    )
    return parse_skill_bytes(data, relative_path=relative_path, limits=limits)
