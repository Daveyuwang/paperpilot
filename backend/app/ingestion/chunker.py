"""
Semantic chunker: splits raw PDF blocks into embedding-ready chunks.
Respects section and paragraph boundaries; merges short blocks.
"""
from __future__ import annotations

MAX_CHUNK_TOKENS = 512   # approximate, based on character count
MIN_CHUNK_CHARS = 80
CHARS_PER_TOKEN = 4      # rough estimate for English academic text


def split_into_chunks(raw_chunks: list[dict]) -> list[dict]:
    """
    Takes the raw block list from the PDF parser and produces
    semantically coherent chunks bounded by section + size limits.
    """
    final_chunks: list[dict] = []
    idx = 0

    for raw in raw_chunks:
        content = raw["content"].strip()
        if not content or content == "[FIGURE]":
            # Keep figure/table chunks as-is
            final_chunks.append({**raw, "chunk_index": idx})
            idx += 1
            continue

        # Split long paragraphs at sentence boundaries
        sub_chunks = _split_by_size(content, MAX_CHUNK_TOKENS * CHARS_PER_TOKEN)

        for sub in sub_chunks:
            if len(sub) < MIN_CHUNK_CHARS:
                # Merge tiny fragments into previous chunk if possible
                if final_chunks and final_chunks[-1]["section_title"] == raw.get("section_title"):
                    final_chunks[-1]["content"] += " " + sub
                    continue
            final_chunks.append({
                "content": sub,
                "section_title": raw.get("section_title"),
                "page_number": raw.get("page_number"),
                "content_type": raw.get("content_type", "text"),
                "bbox": raw.get("bbox"),
                "chunk_index": idx,
            })
            idx += 1

    return final_chunks


def _split_by_size(text: str, max_chars: int) -> list[str]:
    """Split text at sentence boundaries, keeping chunks under max_chars."""
    if len(text) <= max_chars:
        return [text]

    import re
    # Split at sentence boundaries
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = ""

    for sent in sentences:
        if len(current) + len(sent) + 1 <= max_chars:
            current = (current + " " + sent).strip()
        else:
            if current:
                chunks.append(current)
            # If a single sentence is too long, hard-split it
            if len(sent) > max_chars:
                for i in range(0, len(sent), max_chars):
                    chunks.append(sent[i : i + max_chars])
            else:
                current = sent

    if current:
        chunks.append(current)

    return chunks
