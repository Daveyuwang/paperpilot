"""
Unified Langfuse tracing module.

Usage:
    from app.tracing import get_langfuse, create_trace, trace_llm_call, trace_span

    # Create a trace for a request
    trace = create_trace(name="paper_qa", metadata={...})

    # Create a span for an LLM call
    with trace_llm_call(trace, name="generate_answer", model="claude-sonnet-4-6") as span:
        result = await llm.create_text(...)
        span.update(output=result)
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Generator

import structlog

from app.config import get_settings

logger = structlog.get_logger()

_langfuse_client = None


def get_langfuse():
    """Lazy-init singleton Langfuse client. Returns None if tracing is disabled."""
    global _langfuse_client
    settings = get_settings()
    if not settings.enable_tracing:
        return None
    if _langfuse_client is not None:
        return _langfuse_client
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.warning("tracing_disabled_missing_keys")
        return None
    try:
        from langfuse import Langfuse
        _langfuse_client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host or "http://langfuse:3000",
        )
        logger.info("langfuse_initialized", host=settings.langfuse_host)
        return _langfuse_client
    except Exception as e:
        logger.warning("langfuse_init_failed", error=str(e))
        return None


def flush_tracing():
    """Flush pending traces. Call on shutdown."""
    if _langfuse_client:
        try:
            _langfuse_client.flush()
        except Exception:
            pass


class NoopSpan:
    """Fallback when tracing is disabled -- all methods are no-ops."""
    def update(self, **kwargs): pass
    def end(self, **kwargs): pass
    def generation(self, **kwargs): return NoopSpan()
    def span(self, **kwargs): return NoopSpan()
    def score(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


class NoopTrace(NoopSpan):
    """Fallback trace when tracing is disabled."""
    @property
    def id(self): return "noop"


def create_trace(
    *,
    name: str,
    workspace_id: str = "",
    guest_id: str = "",
    session_id: str = "",
    run_id: str = "",
    metadata: dict[str, Any] | None = None,
):
    """Create a Langfuse trace. Returns NoopTrace if tracing is disabled."""
    lf = get_langfuse()
    if not lf:
        return NoopTrace()
    try:
        meta = {
            "workspace_id": workspace_id,
            "guest_id": guest_id,
            "session_id": session_id,
            "run_id": run_id,
            **(metadata or {}),
        }
        return lf.trace(
            name=name,
            metadata=meta,
            session_id=session_id or None,
            user_id=guest_id or None,
        )
    except Exception as e:
        logger.debug("trace_create_failed", error=str(e))
        return NoopTrace()


@contextmanager
def trace_llm_call(
    parent,
    *,
    name: str,
    model: str = "",
    input_data: Any = None,
    metadata: dict[str, Any] | None = None,
) -> Generator:
    """Context manager for tracing an LLM generation call."""
    if isinstance(parent, (NoopSpan, NoopTrace)):
        yield NoopSpan()
        return
    t0 = time.monotonic()
    gen = parent.generation(
        name=name,
        model=model,
        input=input_data,
        metadata=metadata or {},
    )
    try:
        yield gen
    except Exception as e:
        gen.update(
            status_message=str(e),
            level="ERROR",
            completion_start_time=None,
        )
        gen.end()
        raise
    else:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        gen.update(metadata={**(metadata or {}), "duration_ms": elapsed_ms})
        gen.end()


@contextmanager
def trace_span(
    parent,
    *,
    name: str,
    input_data: Any = None,
    metadata: dict[str, Any] | None = None,
) -> Generator:
    """Context manager for tracing a generic span (external API call, processing step, etc.)."""
    if isinstance(parent, (NoopSpan, NoopTrace)):
        yield NoopSpan()
        return
    t0 = time.monotonic()
    span = parent.span(
        name=name,
        input=input_data,
        metadata=metadata or {},
    )
    try:
        yield span
    except Exception as e:
        span.update(status_message=str(e), level="ERROR")
        span.end()
        raise
    else:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        span.update(metadata={**(metadata or {}), "duration_ms": elapsed_ms})
        span.end()


def get_langfuse_callback_handler(trace=None, **kwargs):
    """Get a LangChain callback handler for Langfuse. Returns None if tracing is disabled."""
    lf = get_langfuse()
    if not lf:
        return None
    try:
        from langfuse.callback import CallbackHandler
        handler_kwargs = {}
        if trace and not isinstance(trace, (NoopSpan, NoopTrace)):
            handler_kwargs["trace_id"] = trace.id
        handler_kwargs.update(kwargs)
        return CallbackHandler(
            public_key=get_settings().langfuse_public_key,
            secret_key=get_settings().langfuse_secret_key,
            host=get_settings().langfuse_host or "http://langfuse:3000",
            **handler_kwargs,
        )
    except Exception as e:
        logger.debug("langfuse_callback_handler_failed", error=str(e))
        return None
