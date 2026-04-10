"""Unit tests for citation mapping and text sanitization."""
import pytest
from app.agents.nodes import _sanitize_text


class TestSanitizeText:
    def test_normal_text(self):
        assert _sanitize_text("Hello world") == "Hello world"

    def test_none_returns_fallback(self):
        assert _sanitize_text(None) == ""
        assert _sanitize_text(None, "(citation)") == "(citation)"

    def test_empty_string_returns_fallback(self):
        assert _sanitize_text("", "(citation)") == "(citation)"

    def test_strips_byte_string(self):
        assert _sanitize_text("b'\\xc3\\xa9' test") == "test"

    def test_strips_control_chars(self):
        result = _sanitize_text("hello\x00\x01\x02world")
        assert result == "helloworld"

    def test_strips_whitespace(self):
        assert _sanitize_text("  hello  ") == "hello"

    def test_all_garbage_returns_fallback(self):
        assert _sanitize_text("b'\\xff'", "(citation)") == "(citation)"

    def test_mixed_content(self):
        result = _sanitize_text("§2.1 Introduction\x00")
        assert result == "§2.1 Introduction"

    def test_double_section_symbols(self):
        result = _sanitize_text("§§2.1 Methods")
        assert result == "§§2.1 Methods"


class TestEvidenceEnrichment:
    """Verify that evidence items come out with valid page/section data."""

    def test_enrichment_produces_valid_fields(self):
        # Simulate the enrichment loop from extract_evidence
        chunks = [
            {"chunk_id": "c1", "section_title": "Introduction", "page_number": 3, "bbox": None},
            {"chunk_id": "c2", "section_title": None, "page_number": 5, "bbox": None},
        ]
        raw_evidence = [
            {"chunk_index": 1, "type": "EXPLICIT", "passage": "Test passage", "note": "Relevant"},
            {"chunk_index": 2, "type": "INFERRED", "passage": "Another passage", "note": ""},
        ]

        enriched = []
        for item in raw_evidence:
            idx = item.get("chunk_index", 1) - 1
            if 0 <= idx < len(chunks):
                chunk = chunks[idx]
                enriched.append({
                    "type": item.get("type", "INFERRED"),
                    "passage": _sanitize_text(item.get("passage", ""), "(citation)"),
                    "note": _sanitize_text(item.get("note", "")),
                    "chunk_id": chunk.get("chunk_id"),
                    "section_title": _sanitize_text(chunk.get("section_title")),
                    "page_number": chunk.get("page_number"),
                })

        assert len(enriched) == 2
        assert enriched[0]["page_number"] == 3
        assert enriched[0]["section_title"] == "Introduction"
        assert enriched[1]["page_number"] == 5
        assert enriched[1]["section_title"] == ""  # None -> fallback ""

    def test_out_of_range_chunk_index_skipped(self):
        chunks = [{"chunk_id": "c1", "section_title": "S1", "page_number": 1, "bbox": None}]
        raw_evidence = [{"chunk_index": 5, "type": "EXPLICIT", "passage": "test", "note": ""}]

        enriched = []
        for item in raw_evidence:
            idx = item.get("chunk_index", 1) - 1
            if 0 <= idx < len(chunks):
                enriched.append(item)

        assert len(enriched) == 0
