"""Cross-process single-run guard for Deep Research start/resume operations."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import structlog
from sqlalchemy import text

from app.deep_research.runtime import normalize_deep_research_run_id


logger = structlog.get_logger()
_LOCK_RELEASE_TIMEOUT_SECONDS = 5.0


class DeepResearchRunLockError(RuntimeError):
    """Base class for run-lock failures."""


class DeepResearchRunAlreadyActive(DeepResearchRunLockError):
    """Another worker currently owns this run's execution lease."""


class DeepResearchRunLockUnavailable(DeepResearchRunLockError):
    """The shared database lock service could not be reached."""


def _advisory_lock_id(run_id: str) -> int:
    digest = hashlib.blake2b(
        b"paperpilot:deep-research:run:" + run_id.encode("ascii"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


async def _commit_lock_statement(connection: Any) -> None:
    """End SQLAlchemy's implicit transaction without releasing a session lock.

    ``pg_advisory_lock`` is session-scoped, so it survives ``COMMIT``.  Ending
    the transaction immediately avoids holding an idle transaction open for
    the (potentially long) graph stream.  The small optional-method guard also
    keeps the lock usable with deliberately minimal test connections.
    """

    commit = getattr(connection, "commit", None)
    if commit is not None:
        await commit()


async def _unlock_session(connection: Any, lock_id: int) -> bool:
    result = await connection.execute(
        text("SELECT pg_advisory_unlock(:lock_id)"),
        {"lock_id": lock_id},
    )
    released = bool(result.scalar_one())
    await _commit_lock_statement(connection)
    return released


@asynccontextmanager
async def _default_connection_factory() -> AsyncIterator[Any]:
    # Import lazily so unit tests can inject a connection without creating the
    # application database engine or requiring a live Postgres service.
    from app.db.postgres import engine

    try:
        connection = await engine.connect()
    except Exception as exc:
        raise DeepResearchRunLockUnavailable(
            "Could not connect to the Deep Research run lock service."
        ) from exc
    try:
        yield connection
    finally:
        await connection.close()


@asynccontextmanager
async def deep_research_run_lock(
    run_id: str,
    *,
    connection_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
) -> AsyncIterator[None]:
    """Acquire a non-blocking, session-scoped Postgres advisory lock.

    Holding the same connection for the context duration makes the lock safe
    across API replicas.  A worker crash releases the advisory lock with its
    database session, so a run remains recoverable.
    """

    normalized_run_id = normalize_deep_research_run_id(run_id)
    lock_id = _advisory_lock_id(normalized_run_id)
    factory = connection_factory or _default_connection_factory

    async with factory() as connection:
        try:
            result = await connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": lock_id},
            )
            acquired = bool(result.scalar_one())
            # A session-level advisory lock survives this commit.  Do not keep
            # SQLAlchemy's implicit SELECT transaction open while the graph
            # performs long-running LLM, search, or synthesis work.
            await _commit_lock_statement(connection)
        except Exception as exc:
            raise DeepResearchRunLockUnavailable(
                "Could not acquire the Deep Research run lock."
            ) from exc

        if not acquired:
            raise DeepResearchRunAlreadyActive(
                f"Deep Research run {normalized_run_id} is already active."
            )

        try:
            yield
        finally:
            cancel_requested = False
            unlock_task = asyncio.create_task(
                asyncio.wait_for(
                    _unlock_session(connection, lock_id),
                    timeout=_LOCK_RELEASE_TIMEOUT_SECONDS,
                )
            )
            try:
                # Shield keeps cancellation from abandoning a session lock.
                # Retain and finish the task before the connection is returned
                # to its pool, then propagate cancellation to the caller.
                while not unlock_task.done():
                    try:
                        await asyncio.shield(unlock_task)
                    except asyncio.CancelledError:
                        cancel_requested = True
                if not unlock_task.result():
                    logger.warning(
                        "deep_research_run_lock_not_owned",
                        run_id=normalized_run_id,
                    )
            except BaseException as exc:
                # SQLAlchemy normally returns the physical session to its
                # pool, where a session-level advisory lock would survive.
                # Invalidate on an unlock fault so the backend session closes
                # and PostgreSQL releases the lock before this connection can
                # be reused.
                invalidate = getattr(connection, "invalidate", None)
                if invalidate is not None:
                    try:
                        await asyncio.shield(invalidate())
                    except BaseException:
                        pass
                logger.error(
                    "deep_research_run_lock_release_failed",
                    run_id=normalized_run_id,
                    error_type=type(exc).__name__,
                )
                if not isinstance(exc, Exception):
                    raise
            if cancel_requested:
                raise asyncio.CancelledError()


__all__ = [
    "DeepResearchRunAlreadyActive",
    "DeepResearchRunLockError",
    "DeepResearchRunLockUnavailable",
    "deep_research_run_lock",
]
