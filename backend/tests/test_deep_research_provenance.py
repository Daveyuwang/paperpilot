"""Focused source-capture and evidence-provenance contract tests."""

from __future__ import annotations

from typing import Any

import pytest

from app.deep_research.models import SubQuestion, SubReport, SourceRef
from app.deep_research.nodes import execute as execute_module
from app.deep_research.provenance import (
    MAX_SOURCE_EXCERPT_CHARS,
    build_evidence_inventory,
    normalize_source_url,
    sanitize_source_excerpt,
)


def _sub_report(*, excerpt: str) -> SubReport:
    return SubReport(
        sub_question_id="sq-a",
        question="What does the primary source establish?",
        findings="A model-derived synthesis of several source passages.",
        key_facts=["A model-derived key fact."],
        confidence=0.9,
        gaps="",
        sources=[
            SourceRef(
                url=(
                    "https://reader:password@evidence.example/article"
                    "?view=full&access_token=secret-token#private"
                ),
                title="Primary source",
                excerpt=excerpt,
                published_at="2026-07-01",
                source_type="journal_article",
            )
        ],
    )


def test_source_url_and_excerpt_sanitization_are_bounded() -> None:
    url = normalize_source_url(
        "https://reader:password@Evidence.Example:443/article"
        "?z=2&access_token=secret&a=1#fragment"
    )
    excerpt = sanitize_source_excerpt(
        "Bearer abcdefghijklmnopqrstuvwxyz api_key=raw-secret "
        + ("evidence " * 1000)
    )

    assert url == "https://evidence.example/article?a=1&z=2"
    assert "reader" not in url and "secret" not in url
    assert len(excerpt) <= MAX_SOURCE_EXCERPT_CHARS
    assert "raw-secret" not in excerpt
    assert "abcdefghijklmnopqrstuvwxyz" not in excerpt
    assert "[REDACTED]" in excerpt


def test_inventory_separates_direct_excerpt_from_unbound_derived_summaries() -> None:
    sources, evidence = build_evidence_inventory(
        [_sub_report(excerpt="The primary document states a bounded direct fact.")]
    )

    direct = [unit for unit in evidence if unit.provenance == "source_excerpt"]
    derived = [unit for unit in evidence if unit.provenance == "derived_summary"]
    assert len(sources) == 1
    assert len(direct) == 1
    assert direct[0].kind == "source_excerpt"
    assert direct[0].source_ids == [sources[0].source_id]
    assert sources[0].published_at == "2026-07-01"
    assert sources[0].source_type == "journal_article"
    assert {unit.kind for unit in derived} == {"finding", "key_fact"}
    assert all(unit.source_ids == [] for unit in derived)


class _FakeStructuredLLM:
    def __init__(self, response: SubReport):
        self.response = response
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any) -> SubReport:
        self.calls.append(messages)
        return self.response


@pytest.mark.asyncio
async def test_execute_retains_only_sanitized_bounded_source_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_url = (
        "https://reader:password@evidence.example/article"
        "?view=full&access_token=top-secret#fragment"
    )
    page = (
        "Primary evidence establishes a bounded result. "
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz "
        "api_key=page-secret "
        + ("detail " * 1000)
    )
    question = SubQuestion(
        id="sq-a",
        question="What does the source establish?",
        search_queries=["primary evidence"],
        priority=1,
        rationale="Required evidence.",
    )
    fake_llm = _FakeStructuredLLM(
        SubReport(
            sub_question_id="model-value-is-replaced",
            question="model value is replaced",
            findings="A bounded finding.",
            key_facts=["A bounded fact."],
            confidence=0.9,
            gaps="",
            sources=[],
        )
    )

    async def fake_search(_queries):
        return [
            {
                "url": raw_url,
                "title": "Primary\x00 Source",
                "snippet": "Fallback snippet.",
                "published_at": "2026-07-01T00:00:00Z",
                "source_type": "journal_article",
            }
        ]

    async def fake_fetch(_urls):
        return [(raw_url, page)]

    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(execute_module, "tavily_search", fake_search)
    monkeypatch.setattr(execute_module, "fetch_pages", fake_fetch)
    monkeypatch.setattr(execute_module, "adispatch_custom_event", ignore_event)
    monkeypatch.setattr(
        execute_module,
        "make_structured_llm",
        lambda *_args, **_kwargs: fake_llm,
    )

    report, failure = await execute_module._execute_single(
        question,
        0,
        1,
        {"sub_reports": [], "api_key": "state-secret"},
    )

    assert failure is None
    assert report is not None and len(report.sources) == 1
    source = report.sources[0]
    assert source.url == "https://evidence.example/article?view=full"
    assert source.source_id.startswith("src-")
    assert source.published_at == "2026-07-01T00:00:00Z"
    assert source.source_type == "journal_article"
    assert len(source.excerpt) <= MAX_SOURCE_EXCERPT_CHARS
    retained = source.model_dump_json()
    prompt = str(fake_llm.calls)
    for secret in ("password", "top-secret", "page-secret", "abcdefghijklmnopqrstuvwxyz"):
        assert secret not in retained
        assert secret not in prompt
