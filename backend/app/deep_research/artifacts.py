"""Immutable, ownership-scoped Deep Research artifact persistence.

The graph deliberately does not depend on this module. Runtime integration can
append snapshots at durable boundaries while this store remains independently
testable and usable by recovery/console readers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import (
    DeepResearchArtifactKind,
    DeepResearchArtifactVersion,
    WorkflowRun,
    WorkflowRunType,
)


class ArtifactStoreError(RuntimeError):
    """Base error for fail-closed artifact persistence."""


class ArtifactOwnershipError(ArtifactStoreError):
    """The requested Deep Research run is absent from the ownership scope."""


class DeepResearchArtifactConflictError(ArtifactStoreError):
    """An idempotency key or logical version conflicts with durable state."""


# Explicit alias for callers that prefer the operation-oriented name.
ArtifactWriteConflictError = DeepResearchArtifactConflictError


class ArtifactLineageError(ArtifactStoreError):
    """A parent version is missing, cross-scoped, or non-monotonic."""


class ArtifactIntegrityError(ArtifactStoreError):
    """A stored payload no longer matches its immutable content hash."""


class UnsafeArtifactPayloadError(ArtifactStoreError):
    """A payload contains credentials, private runtime config, or invalid JSON."""


_FORBIDDEN_NORMALIZED_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "proxyauthorization",
        "accesstoken",
        "refreshtoken",
        "authtoken",
        "bearertoken",
        "sessiontoken",
        "clientsecret",
        "secret",
        "privatekey",
        "password",
        "cookie",
        "setcookie",
        "baseurl",
        "llmconfig",
        "modelconfig",
    }
)
_FORBIDDEN_KEY_SUFFIXES = (
    "apikey",
    "accesstoken",
    "refreshtoken",
    "authtoken",
    "bearertoken",
    "sessiontoken",
    "clientsecret",
    "authorization",
    "secret",
    "privatekey",
    "password",
    "cookie",
)
_CREDENTIAL_VALUE_RE = re.compile(
    r"(?i)^(?:(?:bearer|basic)\s+\S{8,}|sk-[a-z0-9_-]{8,}|"
    r"xai-[a-z0-9_-]{8,}|"
    r"AIza[a-z0-9_-]{20,})$"
)
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|(?:proxy[_-]?)?authorization|"
    r"access[_-]?token|refresh[_-]?token|client[_-]?secret|password|"
    r"set[_-]?cookie|cookie)\s*[:=]\s*['\"]?\S{8,}"
)
_HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _is_forbidden_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return normalized in _FORBIDDEN_NORMALIZED_KEYS or normalized.endswith(
        _FORBIDDEN_KEY_SUFFIXES
    )


def _looks_like_credential(value: str) -> bool:
    stripped = value.strip()
    if _CREDENTIAL_VALUE_RE.fullmatch(stripped):
        return True
    if _CREDENTIAL_ASSIGNMENT_RE.search(stripped):
        return True
    if "://" in stripped:
        try:
            parsed = urlsplit(stripped)
        except ValueError:
            return False
        return bool(parsed.scheme and (parsed.username or parsed.password))
    return False


def _validate_json_value(value: Any, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and _looks_like_credential(value):
            raise UnsafeArtifactPayloadError(
                f"artifact payload contains credential-shaped data at {path}"
            )
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsafeArtifactPayloadError(
                f"artifact payload contains a non-finite number at {path}"
            )
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise UnsafeArtifactPayloadError(
                    f"artifact payload has a non-string key at {path}"
                )
            if _is_forbidden_key(key):
                raise UnsafeArtifactPayloadError(
                    f"artifact payload contains private runtime config at {path}.{key}"
                )
            _validate_json_value(item, f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    raise UnsafeArtifactPayloadError(
        f"artifact payload contains unsupported JSON data at {path}"
    )


def validate_artifact_payload(payload: Mapping[str, Any]) -> None:
    """Reject secrets/private configuration and non-canonical JSON values.

    Rejection is intentional: silently redacting data would make the stored
    hash differ from the artifact the workflow believes it persisted.
    """

    if not isinstance(payload, Mapping):
        raise UnsafeArtifactPayloadError("artifact payload must be a JSON object")
    _validate_json_value(payload, "payload")


def _canonical_payload_json(payload: Mapping[str, Any]) -> str:
    validate_artifact_payload(payload)
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise UnsafeArtifactPayloadError(
            "artifact payload is not canonical JSON"
        ) from exc


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    """Return a stable SHA-256 hash after applying the safety gate."""

    encoded = _canonical_payload_json(payload).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_payload_copy(payload: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(_canonical_payload_json(payload))


def _require_string(name: str, value: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty, trimmed string")
    if len(value) > max_length:
        raise ValueError(f"{name} must contain at most {max_length} characters")
    return value


def _require_integer(name: str, value: int, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _coerce_kind(
    artifact_kind: DeepResearchArtifactKind | str,
) -> DeepResearchArtifactKind:
    try:
        return DeepResearchArtifactKind(artifact_kind)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported artifact kind: {artifact_kind!r}") from exc


async def _require_owned_run(
    db: AsyncSession,
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    for_update: bool = False,
) -> WorkflowRun:
    statement = select(WorkflowRun).where(
        WorkflowRun.id == run_id,
        WorkflowRun.workspace_id == workspace_id,
        WorkflowRun.guest_id == guest_id,
        WorkflowRun.run_type == WorkflowRunType.deep_research,
    )
    if for_update:
        statement = statement.with_for_update()
    run = await db.scalar(statement)
    if run is None:
        raise ArtifactOwnershipError("Deep Research run not found in ownership scope")
    return run


async def _find_by_write_key(
    db: AsyncSession,
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    write_key: str,
) -> DeepResearchArtifactVersion | None:
    return await db.scalar(
        select(DeepResearchArtifactVersion).where(
            DeepResearchArtifactVersion.run_id == run_id,
            DeepResearchArtifactVersion.workspace_id == workspace_id,
            DeepResearchArtifactVersion.guest_id == guest_id,
            DeepResearchArtifactVersion.write_key == write_key,
        )
    )


def _verify_stored_artifact(artifact: DeepResearchArtifactVersion) -> None:
    if not _HEX_SHA256_RE.fullmatch(artifact.content_hash):
        raise ArtifactIntegrityError("stored artifact has an invalid content hash")
    try:
        actual_hash = canonical_payload_hash(artifact.payload)
    except UnsafeArtifactPayloadError as exc:
        raise ArtifactIntegrityError(
            "stored artifact violates the payload safety contract"
        ) from exc
    if actual_hash != artifact.content_hash:
        raise ArtifactIntegrityError(
            "stored artifact payload does not match its content hash"
        )


@dataclass(frozen=True, slots=True)
class _ArtifactWriteIdentity:
    workspace_id: str
    guest_id: str
    artifact_kind: DeepResearchArtifactKind
    logical_artifact_id: str
    version_number: int
    plan_version: int
    controller_cycle: int
    schema_version: int
    parent_version_id: str | None
    source_checkpoint_id: str | None
    content_hash: str


@dataclass(frozen=True, slots=True)
class ArtifactAppendSpec:
    """One logical mutation in an atomic artifact persistence boundary."""

    artifact_kind: DeepResearchArtifactKind | str
    logical_artifact_id: str
    write_key: str
    payload: Mapping[str, Any]
    plan_version: int = 0
    controller_cycle: int = 0
    schema_version: int = 1
    source_checkpoint_id: str | None = None
    skip_if_unchanged: bool = False
    expected_version_number: int | None = None
    singleton: bool = False


ArtifactAppendDisposition = Literal["created", "replayed", "unchanged"]


@dataclass(frozen=True, slots=True)
class ArtifactAppendReceipt:
    artifact: DeepResearchArtifactVersion
    disposition: ArtifactAppendDisposition


def _resolve_idempotent_write(
    existing: DeepResearchArtifactVersion,
    *,
    expected: _ArtifactWriteIdentity,
) -> DeepResearchArtifactVersion:
    _verify_stored_artifact(existing)
    actual = _ArtifactWriteIdentity(
        workspace_id=existing.workspace_id,
        guest_id=existing.guest_id,
        artifact_kind=_coerce_kind(existing.artifact_kind),
        logical_artifact_id=existing.logical_artifact_id,
        version_number=existing.version_number,
        plan_version=existing.plan_version,
        controller_cycle=existing.controller_cycle,
        schema_version=existing.schema_version,
        parent_version_id=existing.parent_version_id,
        source_checkpoint_id=existing.source_checkpoint_id,
        content_hash=existing.content_hash,
    )
    if actual != expected:
        raise DeepResearchArtifactConflictError(
            "artifact write key was already used for different content or metadata"
        )
    return existing


async def _require_parent_version(
    db: AsyncSession,
    *,
    parent_version_id: str,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    artifact_kind: DeepResearchArtifactKind,
    logical_artifact_id: str,
    version_number: int,
    plan_version: int,
    controller_cycle: int,
) -> DeepResearchArtifactVersion:
    parent = await db.scalar(
        select(DeepResearchArtifactVersion).where(
            DeepResearchArtifactVersion.id == parent_version_id,
            DeepResearchArtifactVersion.run_id == run_id,
            DeepResearchArtifactVersion.workspace_id == workspace_id,
            DeepResearchArtifactVersion.guest_id == guest_id,
        )
    )
    if parent is None:
        raise ArtifactLineageError("parent artifact not found in ownership scope")
    _verify_stored_artifact(parent)
    if (
        parent.artifact_kind != artifact_kind
        or parent.logical_artifact_id != logical_artifact_id
    ):
        raise ArtifactLineageError(
            "parent artifact must have the same kind and logical identity"
        )
    if (
        parent.version_number != version_number - 1
        or parent.plan_version > plan_version
        or parent.controller_cycle > controller_cycle
    ):
        raise ArtifactLineageError("artifact lineage must be monotonic")
    return parent


async def write_artifact_version(
    db: AsyncSession,
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    artifact_kind: DeepResearchArtifactKind | str,
    logical_artifact_id: str,
    version_number: int,
    write_key: str,
    payload: Mapping[str, Any],
    plan_version: int = 0,
    controller_cycle: int = 0,
    schema_version: int = 1,
    parent_version_id: str | None = None,
    source_checkpoint_id: str | None = None,
) -> DeepResearchArtifactVersion:
    """Append one artifact version without committing the caller's transaction.

    The caller supplies deterministic version/write identifiers. Replaying the
    same ``run_id`` + ``write_key`` with the same canonical payload returns the
    existing row. Reusing it for different content fails closed. A savepoint
    contains concurrent unique-constraint races without rolling back unrelated
    work in the caller's transaction.
    """

    run_id = _require_string("run_id", run_id, max_length=36)
    workspace_id = _require_string("workspace_id", workspace_id, max_length=36)
    guest_id = _require_string("guest_id", guest_id, max_length=64)
    logical_artifact_id = _require_string(
        "logical_artifact_id", logical_artifact_id, max_length=255
    )
    write_key = _require_string("write_key", write_key, max_length=255)
    version_number = _require_integer("version_number", version_number, minimum=1)
    plan_version = _require_integer("plan_version", plan_version, minimum=0)
    controller_cycle = _require_integer(
        "controller_cycle", controller_cycle, minimum=0
    )
    schema_version = _require_integer("schema_version", schema_version, minimum=1)
    kind = _coerce_kind(artifact_kind)
    if parent_version_id is not None:
        parent_version_id = _require_string(
            "parent_version_id", parent_version_id, max_length=36
        )
    if source_checkpoint_id is not None:
        source_checkpoint_id = _require_string(
            "source_checkpoint_id", source_checkpoint_id, max_length=255
        )
    canonical_payload = _canonical_payload_copy(payload)
    content_hash = canonical_payload_hash(canonical_payload)
    write_identity = _ArtifactWriteIdentity(
        workspace_id=workspace_id,
        guest_id=guest_id,
        artifact_kind=kind,
        logical_artifact_id=logical_artifact_id,
        version_number=version_number,
        plan_version=plan_version,
        controller_cycle=controller_cycle,
        schema_version=schema_version,
        parent_version_id=parent_version_id,
        source_checkpoint_id=source_checkpoint_id,
        content_hash=content_hash,
    )

    await _require_owned_run(
        db,
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
    )
    existing = await _find_by_write_key(
        db,
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        write_key=write_key,
    )
    if existing is not None:
        return _resolve_idempotent_write(existing, expected=write_identity)

    if version_number == 1 and parent_version_id is not None:
        raise ArtifactLineageError("artifact version 1 cannot name a parent")
    if version_number > 1 and parent_version_id is None:
        raise ArtifactLineageError(
            "artifact versions after 1 must name the immediately prior version"
        )
    if parent_version_id is not None:
        await _require_parent_version(
            db,
            parent_version_id=parent_version_id,
            run_id=run_id,
            workspace_id=workspace_id,
            guest_id=guest_id,
            artifact_kind=kind,
            logical_artifact_id=logical_artifact_id,
            version_number=version_number,
            plan_version=plan_version,
            controller_cycle=controller_cycle,
        )

    artifact = DeepResearchArtifactVersion(
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        artifact_kind=kind,
        logical_artifact_id=logical_artifact_id,
        version_number=version_number,
        plan_version=plan_version,
        controller_cycle=controller_cycle,
        schema_version=schema_version,
        parent_version_id=parent_version_id,
        source_checkpoint_id=source_checkpoint_id,
        content_hash=content_hash,
        write_key=write_key,
        payload=canonical_payload,
    )
    try:
        async with db.begin_nested():
            db.add(artifact)
            await db.flush()
    except IntegrityError as exc:
        concurrent = await _find_by_write_key(
            db,
            run_id=run_id,
            workspace_id=workspace_id,
            guest_id=guest_id,
            write_key=write_key,
        )
        if concurrent is not None:
            return _resolve_idempotent_write(
                concurrent,
                expected=write_identity,
            )
        raise DeepResearchArtifactConflictError(
            "artifact logical version conflicts with durable state"
        ) from exc
    return artifact


async def _find_latest_logical_version(
    db: AsyncSession,
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    artifact_kind: DeepResearchArtifactKind,
    logical_artifact_id: str,
) -> DeepResearchArtifactVersion | None:
    artifact = await db.scalar(
        select(DeepResearchArtifactVersion)
        .where(
            DeepResearchArtifactVersion.run_id == run_id,
            DeepResearchArtifactVersion.workspace_id == workspace_id,
            DeepResearchArtifactVersion.guest_id == guest_id,
            DeepResearchArtifactVersion.artifact_kind == artifact_kind,
            DeepResearchArtifactVersion.logical_artifact_id
            == logical_artifact_id,
        )
        .order_by(DeepResearchArtifactVersion.version_number.desc())
        .limit(1)
    )
    if artifact is not None:
        _verify_stored_artifact(artifact)
    return artifact


def _validated_append_spec(spec: ArtifactAppendSpec) -> ArtifactAppendSpec:
    kind = _coerce_kind(spec.artifact_kind)
    logical_id = _require_string(
        "logical_artifact_id",
        spec.logical_artifact_id,
        max_length=255,
    )
    write_key = _require_string("write_key", spec.write_key, max_length=255)
    plan_version = _require_integer(
        "plan_version", spec.plan_version, minimum=0
    )
    controller_cycle = _require_integer(
        "controller_cycle", spec.controller_cycle, minimum=0
    )
    schema_version = _require_integer(
        "schema_version", spec.schema_version, minimum=1
    )
    checkpoint_id = spec.source_checkpoint_id
    if checkpoint_id is not None:
        checkpoint_id = _require_string(
            "source_checkpoint_id", checkpoint_id, max_length=255
        )
    expected_version = spec.expected_version_number
    if expected_version is not None:
        expected_version = _require_integer(
            "expected_version_number", expected_version, minimum=1
        )
    payload = _canonical_payload_copy(spec.payload)
    return ArtifactAppendSpec(
        artifact_kind=kind,
        logical_artifact_id=logical_id,
        write_key=write_key,
        payload=payload,
        plan_version=plan_version,
        controller_cycle=controller_cycle,
        schema_version=schema_version,
        source_checkpoint_id=checkpoint_id,
        skip_if_unchanged=bool(spec.skip_if_unchanged),
        expected_version_number=expected_version,
        singleton=bool(spec.singleton),
    )


def _resolve_append_replay(
    existing: DeepResearchArtifactVersion,
    *,
    spec: ArtifactAppendSpec,
    content_hash: str,
) -> ArtifactAppendReceipt:
    _verify_stored_artifact(existing)
    expected_kind = _coerce_kind(spec.artifact_kind)
    if (
        _coerce_kind(existing.artifact_kind) != expected_kind
        or existing.logical_artifact_id != spec.logical_artifact_id
        or existing.plan_version != spec.plan_version
        or existing.controller_cycle != spec.controller_cycle
        or existing.schema_version != spec.schema_version
        or existing.source_checkpoint_id != spec.source_checkpoint_id
        or existing.content_hash != content_hash
        or (
            spec.expected_version_number is not None
            and existing.version_number != spec.expected_version_number
        )
    ):
        raise DeepResearchArtifactConflictError(
            "artifact write key was already used for different content or metadata"
        )
    return ArtifactAppendReceipt(artifact=existing, disposition="replayed")


async def append_artifact_batch(
    db: AsyncSession,
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    specs: Sequence[ArtifactAppendSpec],
) -> list[ArtifactAppendReceipt]:
    """Append one checkpoint boundary inside the caller's transaction.

    The owned run row is locked once so independent logical streams can safely
    allocate their next immutable versions.  Crucially, replay lookup happens
    before latest-version allocation; replay after a commit therefore returns
    the original row instead of incorrectly attempting the next version.
    """

    run_id = _require_string("run_id", run_id, max_length=36)
    workspace_id = _require_string("workspace_id", workspace_id, max_length=36)
    guest_id = _require_string("guest_id", guest_id, max_length=64)
    validated = [_validated_append_spec(spec) for spec in specs]
    identities = [
        (_coerce_kind(spec.artifact_kind), spec.logical_artifact_id)
        for spec in validated
    ]
    if len(identities) != len(set(identities)):
        raise ValueError(
            "one persistence boundary cannot mutate a logical artifact twice"
        )
    if len({spec.write_key for spec in validated}) != len(validated):
        raise ValueError("artifact write keys must be unique within a batch")
    if not validated:
        return []

    await _require_owned_run(
        db,
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        for_update=True,
    )

    receipts: list[ArtifactAppendReceipt] = []
    for spec in validated:
        kind = _coerce_kind(spec.artifact_kind)
        payload_hash = canonical_payload_hash(spec.payload)

        existing = await _find_by_write_key(
            db,
            run_id=run_id,
            workspace_id=workspace_id,
            guest_id=guest_id,
            write_key=spec.write_key,
        )
        if existing is not None:
            receipts.append(
                _resolve_append_replay(
                    existing,
                    spec=spec,
                    content_hash=payload_hash,
                )
            )
            continue

        latest = await _find_latest_logical_version(
            db,
            run_id=run_id,
            workspace_id=workspace_id,
            guest_id=guest_id,
            artifact_kind=kind,
            logical_artifact_id=spec.logical_artifact_id,
        )
        if spec.singleton and latest is not None:
            raise DeepResearchArtifactConflictError(
                "singleton artifact already has a terminal version"
            )
        if (
            spec.skip_if_unchanged
            and latest is not None
            and latest.content_hash == payload_hash
        ):
            receipts.append(
                ArtifactAppendReceipt(artifact=latest, disposition="unchanged")
            )
            continue

        version_number = 1 if latest is None else latest.version_number + 1
        if (
            spec.expected_version_number is not None
            and version_number != spec.expected_version_number
        ):
            raise DeepResearchArtifactConflictError(
                "semantic artifact version does not match durable lineage"
            )

        artifact = await write_artifact_version(
            db,
            run_id=run_id,
            workspace_id=workspace_id,
            guest_id=guest_id,
            artifact_kind=kind,
            logical_artifact_id=spec.logical_artifact_id,
            version_number=version_number,
            write_key=spec.write_key,
            payload=spec.payload,
            plan_version=spec.plan_version,
            controller_cycle=spec.controller_cycle,
            schema_version=spec.schema_version,
            parent_version_id=latest.id if latest is not None else None,
            source_checkpoint_id=spec.source_checkpoint_id,
        )
        receipts.append(
            ArtifactAppendReceipt(artifact=artifact, disposition="created")
        )
    return receipts


async def list_artifact_versions(
    db: AsyncSession,
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    artifact_kind: DeepResearchArtifactKind | str | None = None,
    logical_artifact_id: str | None = None,
    through_controller_cycle: int | None = None,
) -> list[DeepResearchArtifactVersion]:
    """List verified versions visible inside exactly one ownership scope."""

    run_id = _require_string("run_id", run_id, max_length=36)
    workspace_id = _require_string("workspace_id", workspace_id, max_length=36)
    guest_id = _require_string("guest_id", guest_id, max_length=64)
    await _require_owned_run(
        db,
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
    )
    statement = select(DeepResearchArtifactVersion).where(
        DeepResearchArtifactVersion.run_id == run_id,
        DeepResearchArtifactVersion.workspace_id == workspace_id,
        DeepResearchArtifactVersion.guest_id == guest_id,
    )
    if artifact_kind is not None:
        statement = statement.where(
            DeepResearchArtifactVersion.artifact_kind == _coerce_kind(artifact_kind)
        )
    if logical_artifact_id is not None:
        logical_artifact_id = _require_string(
            "logical_artifact_id", logical_artifact_id, max_length=255
        )
        statement = statement.where(
            DeepResearchArtifactVersion.logical_artifact_id == logical_artifact_id
        )
    if through_controller_cycle is not None:
        through_controller_cycle = _require_integer(
            "through_controller_cycle", through_controller_cycle, minimum=0
        )
        statement = statement.where(
            DeepResearchArtifactVersion.controller_cycle
            <= through_controller_cycle
        )
    result = await db.scalars(
        statement.order_by(
            DeepResearchArtifactVersion.controller_cycle,
            DeepResearchArtifactVersion.created_at,
            DeepResearchArtifactVersion.id,
        )
    )
    artifacts = list(result.all())
    for artifact in artifacts:
        _verify_stored_artifact(artifact)
    return artifacts


async def get_artifact_snapshot(
    db: AsyncSession,
    *,
    run_id: str,
    workspace_id: str,
    guest_id: str,
    through_controller_cycle: int | None = None,
) -> list[DeepResearchArtifactVersion]:
    """Return the latest verified version of every logical artifact.

    "Latest" is deterministic by artifact version, controller cycle, plan
    version, creation time, then row id. The returned list is ordered by kind
    and logical identity for stable console/recovery serialization.
    """

    artifacts = await list_artifact_versions(
        db,
        run_id=run_id,
        workspace_id=workspace_id,
        guest_id=guest_id,
        through_controller_cycle=through_controller_cycle,
    )
    latest: dict[
        tuple[DeepResearchArtifactKind, str],
        DeepResearchArtifactVersion,
    ] = {}
    for artifact in artifacts:
        key = (_coerce_kind(artifact.artifact_kind), artifact.logical_artifact_id)
        previous = latest.get(key)
        if previous is None or (
            artifact.version_number,
            artifact.controller_cycle,
            artifact.plan_version,
            artifact.created_at,
            artifact.id,
        ) > (
            previous.version_number,
            previous.controller_cycle,
            previous.plan_version,
            previous.created_at,
            previous.id,
        ):
            latest[key] = artifact

    active_plan = latest.get((DeepResearchArtifactKind.plan, "active-plan"))
    if active_plan is not None and "sub_questions" in active_plan.payload:
        raw_questions = active_plan.payload.get("sub_questions")
        if not isinstance(raw_questions, list):
            raise ArtifactIntegrityError(
                "active plan artifact has malformed sub-question membership"
            )
        active_ids: list[str] = []
        for question in raw_questions:
            if not isinstance(question, Mapping):
                raise ArtifactIntegrityError(
                    "active plan artifact has malformed sub-question membership"
                )
            question_id = question.get("id")
            if not isinstance(question_id, str) or not question_id:
                raise ArtifactIntegrityError(
                    "active plan artifact has malformed sub-question membership"
                )
            active_ids.append(question_id)
        if len(active_ids) != len(set(active_ids)):
            raise ArtifactIntegrityError(
                "active plan artifact has duplicate sub-question membership"
            )
        active_id_set = set(active_ids)
        for key in list(latest):
            kind, logical_id = key
            if kind != DeepResearchArtifactKind.sub_report:
                continue
            if not logical_id.startswith("sub-question:"):
                continue
            if logical_id.removeprefix("sub-question:") not in active_id_set:
                del latest[key]
    return sorted(
        latest.values(),
        key=lambda item: (
            _coerce_kind(item.artifact_kind).value,
            item.logical_artifact_id,
        ),
    )


# Descriptive alias for callers that use snapshot terminology consistently.
snapshot_artifact_versions = get_artifact_snapshot


__all__ = [
    "ArtifactAppendDisposition",
    "ArtifactAppendReceipt",
    "ArtifactAppendSpec",
    "ArtifactIntegrityError",
    "ArtifactLineageError",
    "ArtifactOwnershipError",
    "ArtifactStoreError",
    "ArtifactWriteConflictError",
    "DeepResearchArtifactConflictError",
    "UnsafeArtifactPayloadError",
    "append_artifact_batch",
    "canonical_payload_hash",
    "get_artifact_snapshot",
    "list_artifact_versions",
    "snapshot_artifact_versions",
    "validate_artifact_payload",
    "write_artifact_version",
]
