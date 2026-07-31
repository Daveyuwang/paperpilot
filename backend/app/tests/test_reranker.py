from types import SimpleNamespace

import pytest

from app.retrieval import reranker


def test_model_load_failure_uses_fallback(monkeypatch):
    def raise_offline(_model_name):
        raise OSError("offline")

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(
            reranker_model="missing-model",
            reranker_fallback_enabled=True,
        ),
    )
    monkeypatch.setattr(
        reranker,
        "_load_reranker",
        raise_offline,
    )
    reranker._get_reranker.cache_clear()

    try:
        assert reranker._get_reranker() is None
    finally:
        reranker._get_reranker.cache_clear()


def test_unavailable_reranker_preserves_hybrid_order(monkeypatch):
    candidates = [
        {"chunk_id": "first", "content": "alpha", "score": 0.7},
        {"chunk_id": "second", "content": "beta", "score": 0.6},
        {"chunk_id": "third", "content": "gamma", "score": 0.5},
    ]
    monkeypatch.setattr(reranker, "_get_reranker", lambda: None)

    result = reranker.rerank("question", candidates, top_k=2)

    assert [candidate["chunk_id"] for candidate in result] == ["first", "second"]
    assert all("rerank_score" not in candidate for candidate in candidates)


def test_prediction_failure_respects_fallback_flag(monkeypatch):
    class BrokenReranker:
        def predict(self, _pairs):
            raise RuntimeError("prediction failed")

    candidates = [{"chunk_id": "first", "content": "alpha", "score": 0.7}]
    monkeypatch.setattr(reranker, "_get_reranker", lambda: BrokenReranker())
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(reranker_fallback_enabled=True),
    )
    assert reranker.rerank("question", candidates) == candidates

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(reranker_fallback_enabled=False),
    )
    with pytest.raises(RuntimeError, match="prediction failed"):
        reranker.rerank("question", candidates)
