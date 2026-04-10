"""
Dense embedding via sentence-transformers.
Default config uses a smaller BGE model to stay within demo-tier memory limits.
"""
from __future__ import annotations
import structlog
from functools import lru_cache

logger = structlog.get_logger()


@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import SentenceTransformer
    from app.config import get_settings
    settings = get_settings()
    logger.info("loading_embedding_model", model=settings.embedding_model)
    return SentenceTransformer(settings.embedding_model)


def embed_texts(texts: list[str], batch_size: int | None = None) -> list[list[float]]:
    """Embed a list of texts and return a list of float vectors."""
    if not texts:
        return []
    from app.config import get_settings
    settings = get_settings()
    model = _get_model()
    resolved_batch_size = batch_size or settings.embedding_batch_size
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
    model = _get_model()
    embedding = model.encode(
        f"Represent this sentence for searching relevant passages: {query}",
        normalize_embeddings=True,
    )
    return embedding.tolist()


def embed_chunks(contents: list[str]) -> list[list[float]]:
    """Embed passage-side text for indexing (no prefix needed for passages)."""
    return embed_texts(contents)
