"""
Retrieval evaluation: Recall@K, MRR, nDCG@K.

Usage:
    pytest evals/retrieval_eval.py -v

Dataset format (retrieval_eval_v1.json):
{
  "entries": [
    {
      "query": "What optimizer was used?",
      "paper_id": "abc-123",
      "expected_chunk_ids": ["chunk-1", "chunk-3"]
    }
  ]
}
"""
from __future__ import annotations

import math
import pytest
from typing import Any


def recall_at_k(retrieved_ids: list[str], expected_ids: list[str], k: int) -> float:
    if not expected_ids:
        return 1.0
    top_k = set(retrieved_ids[:k])
    hits = len(top_k & set(expected_ids))
    return hits / len(expected_ids)


def mrr(retrieved_ids: list[str], expected_ids: list[str]) -> float:
    expected_set = set(expected_ids)
    for i, rid in enumerate(retrieved_ids):
        if rid in expected_set:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(retrieved_ids: list[str], expected_ids: list[str], k: int) -> float:
    expected_set = set(expected_ids)

    dcg = 0.0
    for i, rid in enumerate(retrieved_ids[:k]):
        rel = 1.0 if rid in expected_set else 0.0
        dcg += rel / math.log2(i + 2)

    ideal_hits = min(len(expected_ids), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))

    return dcg / idcg if idcg > 0 else 0.0


class TestRetrievalMetrics:
    """Unit tests for metric functions (always run, no dataset needed)."""

    def test_recall_at_k_perfect(self):
        assert recall_at_k(["a", "b", "c"], ["a", "b"], 5) == 1.0

    def test_recall_at_k_partial(self):
        assert recall_at_k(["a", "x", "y"], ["a", "b"], 5) == 0.5

    def test_recall_at_k_miss(self):
        assert recall_at_k(["x", "y", "z"], ["a", "b"], 5) == 0.0

    def test_mrr_first(self):
        assert mrr(["a", "b", "c"], ["a"]) == 1.0

    def test_mrr_second(self):
        assert mrr(["x", "a", "c"], ["a"]) == 0.5

    def test_mrr_miss(self):
        assert mrr(["x", "y", "z"], ["a"]) == 0.0

    def test_ndcg_perfect(self):
        score = ndcg_at_k(["a", "b"], ["a", "b"], 5)
        assert score == pytest.approx(1.0)

    def test_ndcg_empty_expected(self):
        assert ndcg_at_k(["a", "b"], [], 5) == 0.0


def test_retrieval_regression(retrieval_dataset: dict):
    """Run retrieval eval against the dataset.

    This test is skipped if the dataset file is empty or missing.
    Requires a running backend with indexed papers.
    """
    entries = retrieval_dataset.get("entries", [])
    if not entries:
        pytest.skip("No entries in retrieval dataset")

    # These will be populated when running against a live system
    results: dict[str, list[float]] = {
        "recall@5": [],
        "recall@10": [],
        "mrr": [],
        "ndcg@10": [],
    }

    for entry in entries:
        query = entry["query"]
        expected = entry["expected_chunk_ids"]
        # In a live test, call the retrieval system:
        # retrieved = hybrid_retrieve(query, paper_id=entry["paper_id"])
        # For now, this is a placeholder
        retrieved: list[str] = []  # TODO: wire to actual retrieval

        results["recall@5"].append(recall_at_k(retrieved, expected, 5))
        results["recall@10"].append(recall_at_k(retrieved, expected, 10))
        results["mrr"].append(mrr(retrieved, expected))
        results["ndcg@10"].append(ndcg_at_k(retrieved, expected, 10))

    # Compute averages
    avg = {k: sum(v) / len(v) if v else 0 for k, v in results.items()}

    print(f"\n{'='*50}")
    print("Retrieval Evaluation Results:")
    for metric, value in avg.items():
        print(f"  {metric}: {value:.3f}")
    print(f"{'='*50}\n")

    # Regression thresholds (adjust as baseline is established)
    BASELINE = {
        "recall@5": 0.0,  # Set after first run with real data
        "recall@10": 0.0,
        "mrr": 0.0,
        "ndcg@10": 0.0,
    }
    REGRESSION_THRESHOLD = 0.05  # 5% regression tolerance

    for metric, baseline in BASELINE.items():
        if baseline > 0 and avg[metric] < baseline - REGRESSION_THRESHOLD:
            pytest.fail(
                f"Retrieval regression: {metric} dropped from {baseline:.3f} to {avg[metric]:.3f} "
                f"(>{REGRESSION_THRESHOLD*100}% decrease)"
            )
