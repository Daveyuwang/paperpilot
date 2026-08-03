from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
import uuid

import pytest

from app.deep_research.run_lock import (
    DeepResearchRunAlreadyActive,
    DeepResearchRunLockUnavailable,
    _advisory_lock_id,
    deep_research_run_lock,
)
from app.deep_research.runtime import DeepResearchInvalidRunId


class _ScalarResult:
    def __init__(self, value: bool):
        self._value = value

    def scalar_one(self) -> bool:
        return self._value


class _Connection:
    def __init__(self, *, acquire: bool = True, release: bool = True):
        self.acquire = acquire
        self.release = release
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error: Exception | None = None
        self.release_error: BaseException | None = None
        self.invalidated = False
        self.commits = 0

    async def execute(self, statement, parameters):
        sql = str(statement)
        self.calls.append((sql, parameters))
        if self.error is not None:
            raise self.error
        if "pg_try_advisory_lock" in sql:
            return _ScalarResult(self.acquire)
        if "pg_advisory_unlock" in sql:
            if self.release_error is not None:
                raise self.release_error
            return _ScalarResult(self.release)
        raise AssertionError(f"unexpected SQL: {sql}")

    async def invalidate(self) -> None:
        self.invalidated = True

    async def commit(self) -> None:
        self.commits += 1


def _factory(connection: _Connection):
    @asynccontextmanager
    async def connection_factory():
        yield connection

    return connection_factory


@pytest.mark.asyncio
async def test_run_lock_holds_one_connection_and_releases_exact_lock_id() -> None:
    run_id = str(uuid.uuid4())
    connection = _Connection()

    async with deep_research_run_lock(
        run_id,
        connection_factory=_factory(connection),
    ):
        assert len(connection.calls) == 1
        assert "pg_try_advisory_lock" in connection.calls[0][0]
        assert connection.commits == 1

    assert len(connection.calls) == 2
    assert "pg_advisory_unlock" in connection.calls[1][0]
    assert connection.calls[0][1] == connection.calls[1][1]
    assert connection.calls[0][1]["lock_id"] == _advisory_lock_id(run_id)
    assert connection.commits == 2


@pytest.mark.asyncio
async def test_run_lock_busy_fails_without_entering_or_unlocking() -> None:
    run_id = str(uuid.uuid4())
    connection = _Connection(acquire=False)

    with pytest.raises(DeepResearchRunAlreadyActive):
        async with deep_research_run_lock(
            run_id,
            connection_factory=_factory(connection),
        ):
            raise AssertionError("busy lock must not enter")

    assert len(connection.calls) == 1
    assert "pg_try_advisory_lock" in connection.calls[0][0]
    assert connection.commits == 1


@pytest.mark.asyncio
async def test_run_lock_maps_database_failure_to_stable_error() -> None:
    run_id = str(uuid.uuid4())
    connection = _Connection()
    connection.error = RuntimeError("private database details")

    with pytest.raises(DeepResearchRunLockUnavailable):
        async with deep_research_run_lock(
            run_id,
            connection_factory=_factory(connection),
        ):
            raise AssertionError("unavailable lock must not enter")


@pytest.mark.asyncio
async def test_unlock_failure_invalidates_connection_before_pool_reuse() -> None:
    run_id = str(uuid.uuid4())
    connection = _Connection()
    connection.release_error = RuntimeError("unlock failed")

    async with deep_research_run_lock(
        run_id,
        connection_factory=_factory(connection),
    ):
        pass

    assert connection.invalidated is True


@pytest.mark.asyncio
async def test_cancellation_releases_lock_and_propagates() -> None:
    run_id = str(uuid.uuid4())
    connection = _Connection()

    with pytest.raises(asyncio.CancelledError):
        async with deep_research_run_lock(
            run_id,
            connection_factory=_factory(connection),
        ):
            raise asyncio.CancelledError

    assert len(connection.calls) == 2
    assert "pg_try_advisory_lock" in connection.calls[0][0]
    assert "pg_advisory_unlock" in connection.calls[1][0]
    assert connection.calls[0][1] == connection.calls[1][1]


@pytest.mark.asyncio
async def test_run_lock_validates_full_uuid_before_opening_connection() -> None:
    opened = False

    @asynccontextmanager
    async def connection_factory():
        nonlocal opened
        opened = True
        yield _Connection()

    with pytest.raises(DeepResearchInvalidRunId):
        async with deep_research_run_lock(
            "truncated-run-id",
            connection_factory=connection_factory,
        ):
            raise AssertionError("invalid ID must not enter")

    assert opened is False


@pytest.mark.asyncio
async def test_two_concurrent_lock_attempts_have_exactly_one_winner() -> None:
    run_id = str(uuid.uuid4())
    service = SimpleNamespace(locked=False, attempts=0, releases=0)

    class SharedConnection(_Connection):
        async def execute(self, statement, parameters):
            sql = str(statement)
            self.calls.append((sql, parameters))
            if "pg_try_advisory_lock" in sql:
                service.attempts += 1
                if service.locked:
                    return _ScalarResult(False)
                service.locked = True
                return _ScalarResult(True)
            if "pg_advisory_unlock" in sql:
                assert service.locked is True
                service.locked = False
                service.releases += 1
                return _ScalarResult(True)
            raise AssertionError(f"unexpected SQL: {sql}")

    entered = asyncio.Event()
    release = asyncio.Event()

    async def first_worker() -> None:
        async with deep_research_run_lock(
            run_id,
            connection_factory=_factory(SharedConnection()),
        ):
            entered.set()
            await release.wait()

    first = asyncio.create_task(first_worker())
    await entered.wait()

    with pytest.raises(DeepResearchRunAlreadyActive):
        async with deep_research_run_lock(
            run_id,
            connection_factory=_factory(SharedConnection()),
        ):
            raise AssertionError("second worker must not enter")

    release.set()
    await first

    assert service.attempts == 2
    assert service.releases == 1
    assert service.locked is False


def test_full_uuid_threads_map_to_distinct_stable_advisory_keys() -> None:
    prefix = uuid.uuid4().hex[:8]
    run_a = str(uuid.UUID(f"{prefix}{uuid.uuid4().hex[8:]}"))
    run_b = str(uuid.UUID(f"{prefix}{uuid.uuid4().hex[8:]}"))

    assert _advisory_lock_id(run_a) == _advisory_lock_id(run_a)
    assert _advisory_lock_id(run_a) != _advisory_lock_id(run_b)
