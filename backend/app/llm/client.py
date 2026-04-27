from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import httpx
import structlog
from anthropic import AsyncAnthropic
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
    before_sleep_log,
)

from app.llm.types import ChatMessage, ResolvedLLMSettings, ProtocolType

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------

def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient / rate-limit errors that are safe to retry."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 529)
    if isinstance(exc, asyncio.TimeoutError):
        return True
    try:
        from anthropic import RateLimitError, InternalServerError, APIConnectionError
        if isinstance(exc, (RateLimitError, InternalServerError, APIConnectionError)):
            return True
    except ImportError:
        pass
    return False


_llm_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
    before_sleep=before_sleep_log(logger, "warning"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_model_for_protocol(protocol: ProtocolType) -> str:
    if protocol == "openai":
        return "gpt-5.4"
    if protocol == "openai_compatible":
        return "anthropic/claude-sonnet-4-6"
    if protocol == "gemini":
        return "gemini-3-flash-preview"
    return "claude-sonnet-4-6"


def _coerce_messages(system: str | None, messages: list[ChatMessage]) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    if system:
        out.append({"role": "system", "content": system})
    out.extend(messages)
    return out


def _strip_code_fences(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
    if raw.endswith("```"):
        raw = raw.rsplit("\n", 1)[0]
    return raw.strip()


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------

class LLMClient:
    def __init__(self, resolved: ResolvedLLMSettings):
        self.resolved = resolved
        self.trace = None  # Set externally to attach tracing

    @property
    def protocol(self) -> ProtocolType:
        return self.resolved.protocol

    @property
    def model(self) -> str:
        return self.resolved.model or _default_model_for_protocol(self.protocol)

    async def create_text(
        self,
        *,
        system: str | None = None,
        messages: list[ChatMessage],
        max_tokens: int = 1024,
        temperature: float = 0.2,
        trace=None,
    ) -> str:
        parent = trace or self.trace
        if parent:
            from app.tracing import trace_llm_call
            with trace_llm_call(
                parent,
                name="create_text",
                model=self.model,
                input_data={"system": system, "messages": messages},
            ) as gen:
                result = await self._create_text_impl(
                    system=system, messages=messages,
                    max_tokens=max_tokens, temperature=temperature,
                )
                gen.update(output=result)
                return result
        return await self._create_text_impl(
            system=system, messages=messages,
            max_tokens=max_tokens, temperature=temperature,
        )

    @_llm_retry
    async def _create_text_impl(
        self,
        *,
        system: str | None = None,
        messages: list[ChatMessage],
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        if self.protocol == "anthropic":
            client = AsyncAnthropic(
                api_key=self.resolved.api_key,
                timeout=60.0,
            )
            resp = await client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system or "",
                messages=[{"role": m["role"], "content": m["content"]} for m in messages],
            )
            return (resp.content[0].text or "").strip()

        if self.protocol in ("openai", "openai_compatible"):
            return await _openai_compatible_create_text(
                base_url=self.resolved.base_url,
                api_key=self.resolved.api_key,
                model=self.model,
                messages=_coerce_messages(system, messages),
                max_tokens=max_tokens,
                temperature=temperature,
            )

        return await _gemini_create_text(
            base_url=self.resolved.base_url,
            api_key=self.resolved.api_key,
            model=self.model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def create_json(
        self,
        *,
        system: str | None = None,
        messages: list[ChatMessage],
        max_tokens: int = 1024,
        temperature: float = 0.2,
        max_retries: int = 2,
        trace=None,
    ) -> Any:
        """Call the LLM and parse the response as JSON.

        If the response is not valid JSON the LLM is prompted to fix its
        output up to *max_retries* additional times before raising.
        """
        last_error: str | None = None
        for attempt in range(1 + max_retries):
            retry_messages = list(messages)
            if last_error and attempt > 0:
                retry_messages.append({
                    "role": "user",
                    "content": (
                        f"Your previous response was not valid JSON. Error: {last_error}\n"
                        "Please respond with valid JSON only."
                    ),
                })
            text = await self.create_text(
                system=system,
                messages=retry_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                trace=trace,
            )
            cleaned = _strip_code_fences(text)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as e:
                last_error = str(e)
                logger.warning(
                    "json_parse_retry", attempt=attempt, error=last_error
                )
        raise ValueError(
            f"Failed to parse JSON after {max_retries + 1} attempts: {last_error}"
        )

    async def stream_text(
        self,
        *,
        system: str | None = None,
        messages: list[ChatMessage],
        max_tokens: int = 1024,
        temperature: float = 0.2,
        trace=None,
    ) -> AsyncIterator[str]:
        parent = trace or self.trace
        gen_span = None
        if parent:
            from app.tracing import NoopSpan, NoopTrace
            if not isinstance(parent, (NoopSpan, NoopTrace)):
                try:
                    gen_span = parent.generation(
                        name="stream_text",
                        model=self.model,
                        input={"system": system, "messages": messages},
                        metadata={},
                    )
                except Exception:
                    gen_span = None

        collected: list[str] = []
        try:
            async for tok in self._stream_text_impl(
                system=system, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
            ):
                collected.append(tok)
                yield tok
        except Exception as e:
            if gen_span:
                try:
                    gen_span.update(status_message=str(e), level="ERROR")
                    gen_span.end()
                except Exception:
                    pass
            raise
        else:
            if gen_span:
                try:
                    gen_span.update(output="".join(collected))
                    gen_span.end()
                except Exception:
                    pass

    async def _stream_text_impl(
        self,
        *,
        system: str | None = None,
        messages: list[ChatMessage],
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        if self.protocol == "anthropic":
            client = AsyncAnthropic(
                api_key=self.resolved.api_key,
                timeout=60.0,
            )
            async with client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system or "",
                messages=[{"role": m["role"], "content": m["content"]} for m in messages],
            ) as stream:
                async for chunk in stream.text_stream:
                    if chunk:
                        yield chunk
            return

        if self.protocol in ("openai", "openai_compatible"):
            async for tok in _openai_compatible_stream_text(
                base_url=self.resolved.base_url,
                api_key=self.resolved.api_key,
                model=self.model,
                messages=_coerce_messages(system, messages),
                max_tokens=max_tokens,
                temperature=temperature,
            ):
                yield tok
            return

        async for tok in _gemini_stream_text(
            base_url=self.resolved.base_url,
            api_key=self.resolved.api_key,
            model=self.model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        ):
            yield tok


async def _openai_compatible_create_text(
    *,
    base_url: str | None,
    api_key: str,
    model: str,
    messages: list[ChatMessage],
    max_tokens: int,
    temperature: float,
) -> str:
    if not base_url:
        base_url = "https://api.openai.com/v1"
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
        return (data["choices"][0]["message"]["content"] or "").strip()


async def _openai_compatible_stream_text(
    *,
    base_url: str | None,
    api_key: str,
    model: str,
    messages: list[ChatMessage],
    max_tokens: int,
    temperature: float,
) -> AsyncIterator[str]:
    if not base_url:
        base_url = "https://api.openai.com/v1"
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line:
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                delta = obj.get("choices", [{}])[0].get("delta", {})
                tok = delta.get("content")
                if tok:
                    yield tok


async def _gemini_create_text(
    *,
    base_url: str | None,
    api_key: str,
    model: str,
    system: str | None,
    messages: list[ChatMessage],
    max_tokens: int,
    temperature: float,
) -> str:
    host = (base_url or "https://generativelanguage.googleapis.com").rstrip("/")
    url = f"{host}/v1beta/models/{model}:generateContent"
    params = {"key": api_key}

    contents = []
    if system:
        contents.append({"role": "user", "parts": [{"text": system}]})
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        if m["role"] == "system":
            role = "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, params=params, json=payload)
        r.raise_for_status()
        data = r.json()
        try:
            return (data["candidates"][0]["content"]["parts"][0]["text"] or "").strip()
        except Exception:
            return ""


async def _gemini_stream_text(
    *,
    base_url: str | None,
    api_key: str,
    model: str,
    system: str | None,
    messages: list[ChatMessage],
    max_tokens: int,
    temperature: float,
) -> AsyncIterator[str]:
    host = (base_url or "https://generativelanguage.googleapis.com").rstrip("/")
    url = f"{host}/v1beta/models/{model}:streamGenerateContent"
    params = {"key": api_key}

    contents = []
    if system:
        contents.append({"role": "user", "parts": [{"text": system}]})
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        if m["role"] == "system":
            role = "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, params=params, json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                try:
                    tok = obj["candidates"][0]["content"]["parts"][0].get("text")
                except Exception:
                    tok = None
                if tok:
                    yield tok
                await asyncio.sleep(0)
