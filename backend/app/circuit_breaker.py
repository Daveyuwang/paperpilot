"""
Circuit breakers for external API calls.

Each external service gets its own breaker with:
- fail_max=5 (open after 5 failures in the window)
- reset_timeout=30 (try again after 30s)

If pybreaker is not installed the module exposes *no-op* sentinels so
callers can import unconditionally without blowing up.
"""
from __future__ import annotations

import structlog

logger = structlog.get_logger()

try:
    import pybreaker

    class _LogListener(pybreaker.CircuitBreakerListener):
        def state_change(self, cb, old_state, new_state):
            logger.warning(
                "circuit_breaker_state_change",
                breaker=cb.name,
                old_state=old_state.name,
                new_state=new_state.name,
            )

        def failure(self, cb, exc):
            logger.debug("circuit_breaker_failure", breaker=cb.name, error=str(exc))

    _listener = _LogListener()

    tavily_breaker = pybreaker.CircuitBreaker(
        fail_max=5,
        reset_timeout=30,
        name="tavily",
        listeners=[_listener],
    )

    semantic_scholar_breaker = pybreaker.CircuitBreaker(
        fail_max=5,
        reset_timeout=30,
        name="semantic_scholar",
        listeners=[_listener],
    )

    wikipedia_breaker = pybreaker.CircuitBreaker(
        fail_max=5,
        reset_timeout=30,
        name="wikipedia",
        listeners=[_listener],
    )

    PYBREAKER_AVAILABLE = True

except ImportError:
    # ── Fallback no-op stubs so callers never break ──────────────────────
    class _NoOpBreaker:  # type: ignore[no-redef]
        """Transparent pass-through when pybreaker is absent."""

        async def call_async(self, func, *args, **kwargs):
            return await func(*args, **kwargs)

    tavily_breaker = _NoOpBreaker()  # type: ignore[assignment]
    semantic_scholar_breaker = _NoOpBreaker()  # type: ignore[assignment]
    wikipedia_breaker = _NoOpBreaker()  # type: ignore[assignment]
    PYBREAKER_AVAILABLE = False
