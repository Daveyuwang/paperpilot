from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import httpx
import structlog
from anthropic import AsyncAnthropic

from app.llm.types import ChatMessage, ResolvedLLMSettings, ProtocolType

logger = structlog.get_logger()


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


class LLMClient:
    def __init__(self, resolved: ResolvedLLMSettings):
        self.resolved = resolved

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
    ) -> str:
        if self.protocol == "anthropic":
            client = AsyncAnthropic(api_key=self.resolved.api_key)
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
    ) -> Any:
        text = await self.create_text(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        cleaned = _strip_code_fences(text)
        return json.loads(cleaned)

    async def stream_text(
        self,
        *,
        system: str | None = None,
        messages: list[ChatMessage],
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        if self.protocol == "anthropic":
            client = AsyncAnthropic(api_key=self.resolved.api_key)
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

