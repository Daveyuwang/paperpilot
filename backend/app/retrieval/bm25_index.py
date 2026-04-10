"""
BM25 sparse retrieval index built over raw chunk text.
Serialized with pickle and cached in Redis per paper.
"""
from __future__ import annotations
import pickle
import re
import structlog

from rank_bm25 import BM25Okapi

logger = structlog.get_logger()


def tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer."""
    text = text.lower()
    tokens = re.findall(r"\b\w+\b", text)
    return tokens


def build_bm25_index(texts: list[str]) -> BM25Okapi:
    tokenized = [tokenize(t) for t in texts]
    return BM25Okapi(tokenized)


def serialize_bm25(bm25: BM25Okapi, chunk_ids: list[str]) -> bytes:
    return pickle.dumps({"bm25": bm25, "chunk_ids": chunk_ids})


def deserialize_bm25(data: bytes) -> tuple[BM25Okapi, list[str]]:
    obj = pickle.loads(data)
    return obj["bm25"], obj["chunk_ids"]


def bm25_search(
    bm25: BM25Okapi,
    chunk_ids: list[str],
    chunks_content: list[str],
    query: str,
    top_k: int = 10,
) -> list[dict]:
    """
    Run BM25 retrieval and return top-k results with scores.
    chunks_content must correspond 1:1 with chunk_ids.
    """
    tokens = tokenize(query)
    scores = bm25.get_scores(tokens)

    # Pair (score, idx) and sort descending
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]

    results = []
    for idx, score in ranked:
        if score > 0 and idx < len(chunk_ids):
            results.append({
                "chunk_id": chunk_ids[idx],
                "content": chunks_content[idx] if idx < len(chunks_content) else "",
                "score": float(score),
                "source": "bm25",
            })
    return results
