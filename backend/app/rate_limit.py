"""Rate limiting via slowapi, keyed by guest_id header."""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def _guest_id_key(request: Request) -> str:
    return request.headers.get("x-guest-id", get_remote_address(request))


limiter = Limiter(key_func=_guest_id_key)
