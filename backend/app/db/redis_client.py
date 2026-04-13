import json
from typing import Any
import redis.asyncio as aioredis
from app.config import get_settings

settings = get_settings()

_pool: aioredis.ConnectionPool | None = None


def get_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            max_connections=20,
            decode_responses=True,
        )
    return _pool


def get_redis() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=get_pool())


# ── Session state helpers ──────────────────────────────────────────────────

SESSION_TTL = 60 * 60 * 24  # 24 hours
GUEST_SETTINGS_TTL = 60 * 60 * 24  # 24 hours


async def get_session_state(session_id: str) -> dict[str, Any]:
    r = get_redis()
    raw = await r.get(f"session:{session_id}")
    if raw is None:
        return {}
    return json.loads(raw)


async def set_session_state(session_id: str, state: dict[str, Any]) -> None:
    r = get_redis()
    await r.setex(f"session:{session_id}", SESSION_TTL, json.dumps(state))


async def update_session_state(session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    state = await get_session_state(session_id)
    state.update(updates)
    await set_session_state(session_id, state)
    return state


async def delete_session_state(session_id: str) -> None:
    r = get_redis()
    await r.delete(f"session:{session_id}")


# ── Guest settings helpers ────────────────────────────────────────────────

async def get_guest_llm_settings(guest_id: str) -> dict[str, Any]:
    r = get_redis()
    raw = await r.get(f"guest:{guest_id}:llm_settings")
    if raw is None:
        return {}
    return json.loads(raw)


async def set_guest_llm_settings(guest_id: str, settings_obj: dict[str, Any]) -> None:
    r = get_redis()
    await r.setex(f"guest:{guest_id}:llm_settings", GUEST_SETTINGS_TTL, json.dumps(settings_obj))


async def delete_guest_llm_settings(guest_id: str) -> None:
    r = get_redis()
    await r.delete(f"guest:{guest_id}:llm_settings")


# ── BM25 index cache helpers ───────────────────────────────────────────────

async def get_bm25_index(paper_id: str) -> bytes | None:
    r = get_redis()
    return await r.get(f"bm25:{paper_id}")


async def set_bm25_index(paper_id: str, data: bytes) -> None:
    r = get_redis()
    # Use raw bytes client for pickle data
    raw_client = aioredis.Redis(connection_pool=get_pool(), decode_responses=False)
    await raw_client.setex(f"bm25:{paper_id}", 60 * 60 * 72, data)


async def delete_bm25_index(paper_id: str) -> None:
    raw_client = aioredis.Redis(connection_pool=get_pool(), decode_responses=False)
    await raw_client.delete(f"bm25:{paper_id}")
