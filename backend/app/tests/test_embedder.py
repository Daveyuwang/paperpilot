import numpy as np

from app.retrieval import embedder


def test_local_hash_embeddings_are_stable_and_normalized():
    vectors = embedder._local_hash_embeddings(
        ["Grounded retrieval for papers", "Grounded retrieval for papers"],
        dimension=64,
    )

    assert vectors.shape == (2, 64)
    np.testing.assert_array_equal(vectors[0], vectors[1])
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), [1.0, 1.0])


def test_embed_texts_uses_local_fallback(monkeypatch):
    monkeypatch.setattr(embedder, "_get_model", lambda: None)

    vectors = embedder.embed_texts(["alpha beta", "beta gamma"])

    assert len(vectors) == 2
    assert len(vectors[0]) > 0
    assert vectors[0] != vectors[1]
