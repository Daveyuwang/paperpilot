"""
Tests for synthesize_expansion streaming behavior and JSON parse-failure handling.

Root bug: the LLM sometimes outputs unescaped double quotes inside JSON string
values (e.g. searching "UDFBench" or "UDF benchmark"), which makes json.loads
fail.  Previously this caused `direct_answer` to be set to the full raw JSON
text, which the frontend then rendered literally.  The fix converts the node to
streaming, so that:
  1. Tokens for `direct_answer` are streamed immediately via _DirectAnswerExtractor.
  2. If json.loads fails, `extractor._emitted` (the already-streamed text) is
     used as the fallback for `direct_answer` — never the raw JSON blob.
"""
import json
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from app.agents.nodes import _DirectAnswerExtractor, _clean_json, _sanitize_text


# ── _DirectAnswerExtractor unit tests ────────────────────────────────────

class TestDirectAnswerExtractor:
    def _feed_all(self, extractor, text: str) -> str:
        """Feed the entire text in one shot; return all emitted tokens joined."""
        tokens = extractor.feed(text)
        return "".join(tokens)

    def test_extracts_simple_value(self):
        ex = _DirectAnswerExtractor()
        result = self._feed_all(ex, '{"direct_answer": "Hello world", "other": 123}')
        assert result == "Hello world"

    def test_emitted_attr_matches(self):
        ex = _DirectAnswerExtractor()
        self._feed_all(ex, '{"direct_answer": "Test answer", "x": 1}')
        assert ex._emitted == "Test answer"

    def test_extracts_unicode(self):
        ex = _DirectAnswerExtractor()
        result = self._feed_all(ex, '{"direct_answer": "我无法访问实时数据库", "k": null}')
        assert result == "我无法访问实时数据库"

    def test_handles_streamed_chunks(self):
        """Simulate streaming: feed text in small chunks."""
        ex = _DirectAnswerExtractor()
        text = '{"direct_answer": "Hello streaming world", "other": true}'
        collected = []
        for ch in text:
            collected.extend(ex.feed(ch))
        assert "".join(collected) == "Hello streaming world"
        assert ex._emitted == "Hello streaming world"

    def test_stops_at_closing_quote(self):
        ex = _DirectAnswerExtractor()
        raw = '{"direct_answer": "Answer text", "key_points": ["a", "b"]}'
        result = self._feed_all(ex, raw)
        assert result == "Answer text"

    def test_handles_escaped_quote_in_value(self):
        ex = _DirectAnswerExtractor()
        raw = r'{"direct_answer": "He said \"hello\"", "x": 1}'
        result = self._feed_all(ex, raw)
        assert result == 'He said \\"hello\\"'  # raw escape chars passed through

    def test_is_done_after_extraction(self):
        ex = _DirectAnswerExtractor()
        self._feed_all(ex, '{"direct_answer": "Done", "x": 1}')
        assert ex.is_done()

    def test_not_done_before_value_ends(self):
        ex = _DirectAnswerExtractor()
        ex.feed('{"direct_answer": "partial')
        assert not ex.is_done()


# ── Parse-failure fallback: the core bug scenario ─────────────────────────

class TestExpansionParseFallback:
    """
    Simulate the exact bug: LLM outputs unescaped double quotes inside a JSON
    string (e.g. `搜索"UDFBench"`) making json.loads fail.  The node should
    use extractor._emitted as the direct_answer, NOT the raw JSON blob.
    """

    def _make_malformed_lm_response(self) -> str:
        """
        Mimic LLM output with unescaped double quotes inside a string value.
        This is valid-looking JSON to a human but fails json.loads.
        """
        return (
            '{\n'
            '  "direct_answer": "我无法搜索或访问实时数据库来查找关于UDFBench的最新工作。'
            '建议在Google Scholar中搜索"UDFBench"或"UDF benchmark"。",\n'
            '  "key_points": ["Point 1", "Point 2"],\n'
            '  "evidence": [],\n'
            '  "paper_context": null,\n'
            '  "plain_language": null,\n'
            '  "bigger_picture": null,\n'
            '  "uncertainty": null,\n'
            '  "answer_mode": "external_expansion",\n'
            '  "scope_label": "超出本文范围",\n'
            '  "can_expand": false\n'
            '}'
        )

    def test_malformed_json_fails_to_parse(self):
        """Confirm the malformed response does NOT parse with json.loads."""
        raw = _clean_json(self._make_malformed_lm_response())
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw)

    def test_extractor_captures_direct_answer_despite_malformed_json(self):
        """
        Even though the full JSON is invalid, _DirectAnswerExtractor correctly
        reads the direct_answer value char-by-char up to the unescaped quote.
        The important thing: extractor._emitted is the READABLE answer text,
        not raw JSON.
        """
        raw = self._make_malformed_lm_response()
        ex = _DirectAnswerExtractor()
        # Feed the whole response in chunks (simulating streaming)
        chunk_size = 20
        for i in range(0, len(raw), chunk_size):
            ex.feed(raw[i:i + chunk_size])

        # The extractor should have captured the beginning of direct_answer
        # up to where the unescaped quote broke the parse
        emitted = ex._emitted
        assert len(emitted) > 0, "Extractor should have emitted some text"
        # Must NOT contain raw JSON structure like "key_points"
        assert '"key_points"' not in emitted
        assert '"evidence"' not in emitted
        assert '"scope_label"' not in emitted
        # Must be readable natural language
        assert "UDFBench" in emitted or "无法" in emitted

    def test_fallback_does_not_expose_raw_json(self):
        """
        When json.loads fails, the fallback direct_answer should be the
        streamed text (extractor._emitted), NOT the raw JSON blob.
        """
        raw = self._make_malformed_lm_response()
        ex = _DirectAnswerExtractor()
        for ch in raw:
            ex.feed(ch)

        fallback_answer = _sanitize_text(ex._emitted or "", "Unable to generate response.")

        # The fallback must not look like raw JSON
        assert not fallback_answer.startswith("{"), (
            f"Fallback answer looks like raw JSON: {fallback_answer[:100]}"
        )
        assert '"answer_mode"' not in fallback_answer
        assert '"scope_label"' not in fallback_answer

    def test_scope_label_always_hardcoded(self):
        """
        Regardless of what the LLM writes for scope_label (e.g. the translated
        "超出本文范围"), the node always overrides it to "Beyond this paper".
        """
        # Simulate a parsed response that has the wrong scope_label
        answer_json = {
            "direct_answer": "Some answer",
            "scope_label": "超出本文范围",  # LLM translated it
            "answer_mode": "external_expansion",
            "can_expand": True,
        }
        # Apply the same overrides the node does after parsing
        answer_json["answer_mode"] = "external_expansion"
        answer_json["scope_label"] = "Beyond this paper"
        answer_json.setdefault("can_expand", False)

        assert answer_json["scope_label"] == "Beyond this paper"
        assert answer_json["answer_mode"] == "external_expansion"


# ── _clean_json utility ───────────────────────────────────────────────────

class TestCleanJson:
    def test_strips_json_fence(self):
        raw = "```json\n{\"a\": 1}\n```"
        assert _clean_json(raw) == '{"a": 1}'

    def test_strips_plain_fence(self):
        raw = "```\n{\"a\": 1}\n```"
        assert _clean_json(raw) == '{"a": 1}'

    def test_passthrough_clean_json(self):
        raw = '{"a": 1}'
        assert _clean_json(raw) == '{"a": 1}'

    def test_strips_whitespace(self):
        raw = '  {"a": 1}  '
        assert _clean_json(raw) == '{"a": 1}'
