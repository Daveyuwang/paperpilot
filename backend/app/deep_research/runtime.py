"""Durable, version-bound execution runtime for the Deep Research graph.

Invocation credentials live in :class:`DeepResearchContext` and are passed to
LangGraph at execution time.  They are deliberately absent from both the graph
configuration and checkpointed state.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

# LangGraph reads this environment variable while importing its msgpack
# implementation.  Keep the process fail-closed even if a deployment supplied
# a permissive value; the serializer below also carries an explicit allowlist.
os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer  # noqa: E402
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer  # noqa: E402

from app.deep_research.context import (  # noqa: E402
    DEEP_RESEARCH_GRAPH_VERSION,
    DeepResearchContext,
)
from app.deep_research.graph import (  # noqa: E402
    DEEP_RESEARCH_RECURSION_LIMIT,
    build_graph,
)

if TYPE_CHECKING:
    from app.config import Settings


GRAPH_VERSION_METADATA_KEY = "deep_research_graph_version"
WORKSPACE_METADATA_KEY = "deep_research_workspace_id"
GUEST_METADATA_KEY = "deep_research_guest_id"
DEEP_RESEARCH_DURABILITY = "sync"
_CHECKPOINT_MIGRATION_LOCK_ID = 0x445243484B50544D  # "DRCHKPTM"

# Checkpoint state contains only these application-defined Pydantic models.
# Strict msgpack blocks every other application constructor instead of falling
# back to pickle or dynamically importing attacker-selected classes.
_ALLOWED_CHECKPOINT_TYPES: tuple[tuple[str, str], ...] = (
    ("app.deep_research.models", "SubQuestion"),
    ("app.deep_research.models", "Plan"),
    ("app.deep_research.models", "RepairPlan"),
    ("app.deep_research.models", "SourceRef"),
    ("app.deep_research.models", "SubReport"),
    ("app.deep_research.models", "ReportSection"),
    ("app.deep_research.models", "ResearchReport"),
    ("app.deep_research.models", "PreSynthesisScores"),
    ("app.deep_research.models", "EvidenceIssue"),
    ("app.deep_research.models", "EvidenceRepairDirective"),
    ("app.deep_research.models", "PreSynthesisEvaluation"),
    ("app.deep_research.models", "PreSynthesisEvaluationRun"),
    ("app.deep_research.models", "ReportSegment"),
    ("app.deep_research.models", "EvidenceSource"),
    ("app.deep_research.models", "EvidenceUnit"),
    ("app.deep_research.models", "ClaimEvidenceReference"),
    ("app.deep_research.models", "ClaimCitationAudit"),
    ("app.deep_research.models", "AtomicClaimAudit"),
    ("app.deep_research.models", "ReportSegmentAudit"),
    ("app.deep_research.models", "PostSynthesisScores"),
    ("app.deep_research.models", "ReportEvaluationIssue"),
    ("app.deep_research.models", "PostSynthesisEvaluation"),
    ("app.deep_research.models", "PostSynthesisEvaluationRun"),
    ("app.deep_research.models", "RepairStage"),
    ("app.deep_research.models", "BudgetSnapshot"),
    ("app.deep_research.models", "RoutingDecision"),
    ("app.deep_research.models", "PostSynthesisRoutingDecision"),
    ("app.deep_research.models", "ReportSegmentRevision"),
    ("app.deep_research.models", "ReportRevisionPatch"),
)


class DeepResearchRuntimeError(RuntimeError):
    """Base class for runtime errors safe to map to API responses."""


class DeepResearchInvalidRunId(DeepResearchRuntimeError):
    """The public run ID is not a full canonical UUID."""


class DeepResearchCheckpointNotFound(DeepResearchRuntimeError):
    """No restorable checkpoint exists for a run."""


class DeepResearchCheckpointAlreadyExists(DeepResearchRuntimeError):
    """Fresh input was supplied for an existing checkpoint thread."""


class DeepResearchGraphVersionMismatch(DeepResearchRuntimeError):
    """A checkpoint belongs to a different graph contract version."""

    def __init__(self, *, expected: str, actual: str | None) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Deep Research checkpoint graph version mismatch: "
            f"expected {expected!r}, found {actual!r}."
        )


class DeepResearchContextMismatch(DeepResearchRuntimeError):
    """Invocation context is cross-bound to a different run or graph."""


class DeepResearchCheckpointSetupRequired(DeepResearchRuntimeError):
    """The Postgres checkpoint schema is absent or behind this release."""


class DeepResearchRuntimeNotInitialized(DeepResearchRuntimeError):
    """Application lifespan has not initialized the shared runtime."""


class DeepResearchDurabilityRequired(DeepResearchRuntimeError):
    """Production cannot silently advertise resume with process-local state."""


def normalize_deep_research_run_id(run_id: str) -> str:
    """Return a full canonical UUID or fail before checkpoint access."""

    try:
        parsed = uuid.UUID(str(run_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise DeepResearchInvalidRunId("Deep Research run_id must be a UUID.") from exc
    canonical = str(parsed)
    if str(run_id) != canonical:
        raise DeepResearchInvalidRunId(
            "Deep Research run_id must use the full canonical UUID form."
        )
    return canonical


def _strict_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        pickle_fallback=False,
        allowed_msgpack_modules=_ALLOWED_CHECKPOINT_TYPES,
    )


def _checkpoint_database_url(settings: Settings) -> str:
    raw = (
        settings.deep_research_checkpoint_database_url
        or settings.database_url_sync
        or settings.database_url
    )
    for driver in ("+asyncpg", "+psycopg", "+psycopg_async"):
        raw = raw.replace(driver, "", 1)
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql://", 1)
    if not raw.startswith("postgresql://"):
        raise DeepResearchRuntimeError(
            "Deep Research checkpoint storage requires a PostgreSQL URL."
        )
    return raw


def _encryption_key(settings: Settings) -> bytes:
    key = settings.deep_research_checkpoint_aes_key.encode("utf-8")
    if len(key) not in {16, 24, 32}:
        raise DeepResearchRuntimeError(
            "DEEP_RESEARCH_CHECKPOINT_AES_KEY must encode to 16, 24, or 32 bytes."
        )
    return key


async def _verify_checkpoint_schema(pool: Any, saver: Any) -> None:
    """Fail startup when migration-owned checkpoint tables are not current."""

    try:
        async with pool.connection() as connection:
            result = await connection.execute(
                "SELECT v FROM checkpoint_migrations ORDER BY v DESC LIMIT 1"
            )
            row = await result.fetchone()
    except Exception as exc:
        raise DeepResearchCheckpointSetupRequired(
            "LangGraph checkpoint schema is missing; run checkpoint setup first."
        ) from exc

    actual = int(row["v"]) if row is not None else -1
    expected = len(saver.MIGRATIONS) - 1
    if actual != expected:
        raise DeepResearchCheckpointSetupRequired(
            "LangGraph checkpoint schema is not current: "
            f"expected migration {expected}, found {actual}."
        )


async def _setup_checkpoint_schema(pool: Any, serde: Any) -> None:
    """Serialize native checkpointer setup across application replicas."""

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with pool.connection() as connection:
        await connection.execute(
            "SELECT pg_advisory_lock(%s)", (_CHECKPOINT_MIGRATION_LOCK_ID,)
        )
        try:
            await AsyncPostgresSaver(connection, serde=serde).setup()
        finally:
            await connection.execute(
                "SELECT pg_advisory_unlock(%s)", (_CHECKPOINT_MIGRATION_LOCK_ID,)
            )


def _callbacks_config(callbacks: Any) -> Any:
    if callbacks is None:
        return None
    if isinstance(callbacks, Sequence) and not isinstance(
        callbacks, (str, bytes, bytearray)
    ):
        return list(callbacks)
    return [callbacks]


class DeepResearchRuntime:
    """Versioned facade around one async, checkpointed graph instance."""

    def __init__(
        self,
        compiled_graph: Any,
        checkpointer: Any,
        *,
        graph_version: str = DEEP_RESEARCH_GRAPH_VERSION,
        backend: str = "memory",
        close_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.compiled_graph = compiled_graph
        self.checkpointer = checkpointer
        self.graph_version = graph_version
        self.backend = backend
        self._close_callback = close_callback
        self._closed = False

    @classmethod
    async def create(
        cls,
        settings: Settings | None = None,
        *,
        checkpointer: Any | None = None,
        graph_builder: Callable[[], Any] = build_graph,
    ) -> DeepResearchRuntime:
        """Build the runtime with Postgres in enabled deployments, memory otherwise."""

        if settings is None:
            from app.config import get_settings

            settings = get_settings()

        backend = "memory"
        close_callback: Callable[[], Awaitable[None]] | None = None

        if (
            checkpointer is None
            and settings.environment.lower() == "production"
            and not settings.deep_research_checkpoint_enabled
        ):
            raise DeepResearchDurabilityRequired(
                "Production requires DEEP_RESEARCH_CHECKPOINT_ENABLED=true."
            )
        if (
            checkpointer is None
            and settings.environment.lower() == "production"
            and settings.deep_research_checkpoint_auto_setup
        ):
            raise DeepResearchCheckpointSetupRequired(
                "Production requires DEEP_RESEARCH_CHECKPOINT_AUTO_SETUP=false; "
                "run native checkpoint migrations as a controlled pre-deploy step."
            )

        if checkpointer is None and (
            not settings.deep_research_checkpoint_enabled
            or settings.environment.lower() == "test"
        ):
            checkpointer = InMemorySaver(serde=_strict_serializer())

        if checkpointer is None:
            # These dependencies are intentionally imported only for the
            # durable backend so lightweight/test processes can use memory.
            encryption_key = _encryption_key(settings)
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg.rows import dict_row
            from psycopg_pool import AsyncConnectionPool

            strict_serde = _strict_serializer()
            encrypted_serde = EncryptedSerializer.from_pycryptodome_aes(
                serde=strict_serde,
                key=encryption_key,
            )
            pool = AsyncConnectionPool(
                conninfo=_checkpoint_database_url(settings),
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                },
                min_size=1,
                max_size=settings.deep_research_checkpoint_pool_size,
                open=False,
            )
            try:
                await pool.open(wait=True)
                checkpointer = AsyncPostgresSaver(pool, serde=encrypted_serde)
                if settings.deep_research_checkpoint_auto_setup:
                    await _setup_checkpoint_schema(pool, encrypted_serde)
                await _verify_checkpoint_schema(pool, checkpointer)
            except BaseException:
                await pool.close()
                raise

            async def close_pool() -> None:
                await pool.close()

            close_callback = close_pool
            backend = "postgres"

        compiled_graph = graph_builder().compile(checkpointer=checkpointer)
        return cls(
            compiled_graph,
            checkpointer,
            graph_version=DEEP_RESEARCH_GRAPH_VERSION,
            backend=backend,
            close_callback=close_callback,
        )

    def _config(
        self,
        run_id: str,
        callbacks: Any = None,
        context: DeepResearchContext | None = None,
    ) -> dict[str, Any]:
        metadata = {GRAPH_VERSION_METADATA_KEY: self.graph_version}
        if context is not None:
            metadata.update(
                {
                    WORKSPACE_METADATA_KEY: context["workspace_id"],
                    GUEST_METADATA_KEY: context["guest_id"],
                }
            )
        config: dict[str, Any] = {
            "configurable": {"thread_id": run_id},
            "metadata": metadata,
            "recursion_limit": DEEP_RESEARCH_RECURSION_LIMIT,
        }
        configured_callbacks = _callbacks_config(callbacks)
        if configured_callbacks is not None:
            config["callbacks"] = configured_callbacks
        return config

    def _validate_context(
        self,
        *,
        run_id: str,
        context: DeepResearchContext,
    ) -> None:
        if context.get("run_id") != run_id:
            raise DeepResearchContextMismatch(
                "Invocation context run_id does not match the checkpoint thread."
            )
        if context.get("graph_version") != self.graph_version:
            raise DeepResearchContextMismatch(
                "Invocation context graph_version does not match the runtime."
            )

    def _validate_snapshot_version(self, snapshot: Any) -> None:
        metadata = snapshot.metadata or {}
        actual = metadata.get(GRAPH_VERSION_METADATA_KEY)
        if actual != self.graph_version:
            raise DeepResearchGraphVersionMismatch(
                expected=self.graph_version,
                actual=actual,
            )

    def _validate_snapshot_owner(
        self,
        snapshot: Any,
        context: DeepResearchContext,
    ) -> None:
        metadata = snapshot.metadata or {}
        if (
            metadata.get(WORKSPACE_METADATA_KEY) != context.get("workspace_id")
            or metadata.get(GUEST_METADATA_KEY) != context.get("guest_id")
        ):
            raise DeepResearchContextMismatch(
                "Invocation context owner does not match the checkpoint thread."
            )

    async def _snapshot_or_none(
        self,
        run_id: str,
        context: DeepResearchContext | None = None,
    ) -> Any | None:
        snapshot = await self.compiled_graph.aget_state(self._config(run_id))
        if snapshot.created_at is None:
            return None
        self._validate_snapshot_version(snapshot)
        if context is not None:
            self._validate_snapshot_owner(snapshot, context)
        return snapshot

    async def astream_events(
        self,
        input_state_or_none: Any | None,
        run_id: str,
        context: DeepResearchContext,
        callbacks: Any = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Start or resume a run while keeping credentials outside checkpoints."""

        if self._closed:
            raise DeepResearchRuntimeNotInitialized("Deep Research runtime is closed.")
        normalized_run_id = normalize_deep_research_run_id(run_id)
        self._validate_context(run_id=normalized_run_id, context=context)
        existing = await self._snapshot_or_none(normalized_run_id, context)
        if input_state_or_none is None and existing is None:
            raise DeepResearchCheckpointNotFound(
                f"No checkpoint exists for Deep Research run {normalized_run_id}."
            )
        if input_state_or_none is not None and existing is not None:
            raise DeepResearchCheckpointAlreadyExists(
                f"Checkpoint already exists for Deep Research run {normalized_run_id}; "
                "resume with null input."
            )

        config = self._config(normalized_run_id, callbacks, context)
        async for event in self.compiled_graph.astream_events(
            input_state_or_none,
            version="v2",
            config=config,
            context=context,
            durability=DEEP_RESEARCH_DURABILITY,
        ):
            yield event

    async def aget_state(self, run_id: str) -> Any:
        """Return a version-compatible checkpoint snapshot for a run."""

        normalized_run_id = normalize_deep_research_run_id(run_id)
        snapshot = await self._snapshot_or_none(normalized_run_id)
        if snapshot is None:
            raise DeepResearchCheckpointNotFound(
                f"No checkpoint exists for Deep Research run {normalized_run_id}."
            )
        return snapshot

    async def adelete_thread(self, run_id: str) -> None:
        """Idempotently remove a full checkpoint thread without deserializing it."""

        normalized_run_id = normalize_deep_research_run_id(run_id)
        await self.checkpointer.adelete_thread(normalized_run_id)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._close_callback is not None:
            await self._close_callback()


_shared_runtime: DeepResearchRuntime | None = None
_runtime_init_lock = asyncio.Lock()


async def initialize_deep_research_runtime(
    settings: Settings | None = None,
) -> DeepResearchRuntime:
    """Initialize the process-wide runtime during application lifespan."""

    global _shared_runtime
    async with _runtime_init_lock:
        if _shared_runtime is None:
            _shared_runtime = await DeepResearchRuntime.create(settings)
        return _shared_runtime


def get_deep_research_runtime() -> DeepResearchRuntime:
    if _shared_runtime is None:
        raise DeepResearchRuntimeNotInitialized(
            "Deep Research runtime has not been initialized by application lifespan."
        )
    return _shared_runtime


async def shutdown_deep_research_runtime() -> None:
    """Close durable resources and make a later clean re-initialization possible."""

    global _shared_runtime
    async with _runtime_init_lock:
        runtime = _shared_runtime
        _shared_runtime = None
    if runtime is not None:
        await runtime.aclose()


__all__ = [
    "DeepResearchCheckpointAlreadyExists",
    "DeepResearchCheckpointNotFound",
    "DeepResearchCheckpointSetupRequired",
    "DeepResearchContextMismatch",
    "DeepResearchDurabilityRequired",
    "DeepResearchGraphVersionMismatch",
    "DeepResearchInvalidRunId",
    "DeepResearchRuntime",
    "DeepResearchRuntimeError",
    "DeepResearchRuntimeNotInitialized",
    "DEEP_RESEARCH_DURABILITY",
    "DEEP_RESEARCH_RECURSION_LIMIT",
    "GRAPH_VERSION_METADATA_KEY",
    "GUEST_METADATA_KEY",
    "WORKSPACE_METADATA_KEY",
    "get_deep_research_runtime",
    "initialize_deep_research_runtime",
    "normalize_deep_research_run_id",
    "shutdown_deep_research_runtime",
]
