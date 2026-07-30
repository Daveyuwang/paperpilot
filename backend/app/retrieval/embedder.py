"""Dense embeddings with an offline-safe local fallback."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from functools import lru_cache

import numpy as np
import structlog

logger = structlog.get_logger()

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _local_hash_embeddings(texts: list[str], dimension: int) -> np.ndarray:
    """Create stable, normalized lexical vectors without external model files."""
    if dimension <= 0:
        raise ValueError("embedding_dimension must be greater than zero")

    vectors = np.zeros((len(texts), dimension), dtype=np.float32)
    for row, text in enumerate(texts):
        normalized = unicodedata.normalize("NFKC", text).lower()
        tokens = _TOKEN_RE.findall(normalized)
        compact = "".join(normalized.split())
        features: list[tuple[str, float]] = [(f"word:{token}", 1.0) for token in tokens]
        features.extend(
            (f"bigram:{left}\u0000{right}", 0.75)
            for left, right in zip(tokens, tokens[1:])
        )
        features.extend(
            (f"char3:{compact[index:index + 3]}", 0.35)
            for index in range(max(0, len(compact) - 2))
        )
        if not features:
            features.append(("empty", 1.0))

        for feature, weight in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            column = int.from_bytes(digest[:4], "little") % dimension
            sign = -1.0 if digest[4] & 1 else 1.0
            vectors[row, column] += sign * weight

        norm = float(np.linalg.norm(vectors[row]))
        if norm > 0:
            vectors[row] /= norm

    return vectors


@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import SentenceTransformer
    from app.config import get_settings

    settings = get_settings()
    logger.info("loading_embedding_model", model=settings.embedding_model)
    try:
        return SentenceTransformer(settings.embedding_model)
    except Exception as exc:
        if not settings.embedding_fallback_enabled:
            raise
        logger.warning(
            "embedding_model_unavailable_using_local_fallback",
            model=settings.embedding_model,
            error=str(exc),
        )
        return None


def embed_texts(texts: list[str], batch_size: int | None = None) -> list[list[float]]:
    """Embed a list of texts and return a list of float vectors."""
    if not texts:
        return []
    from app.config import get_settings
    settings = get_settings()
    model = _get_model()
    resolved_batch_size = batch_size or settings.embedding_batch_size
    if model is None:
        return _local_hash_embeddings(texts, settings.embedding_dimension).tolist()
    # BGE models benefit from the query prefix for retrieval
    embeddings = model.encode(
        texts,
        batch_size=resolved_batch_size,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    return embeddings.tolist()


def embed_query(query: str) -> list[float]:
    """Embed a single query with the BGE query prefix."""
    from app.config import get_settings

    model = _get_model()
    if model is None:
        return _local_hash_embeddings([query], get_settings().embedding_dimension)[0].tolist()
    embedding = model.encode(
        f"Represent this sentence for searching relevant passages: {query}",
        normalize_embeddings=True,
    )
    return embedding.tolist()


def embed_chunks(contents: list[str]) -> list[list[float]]:
    """Embed passage-side text for indexing (no prefix needed for passages)."""
    return embed_texts(contents)
