"""
Deep Research workflow evaluation.

Process metrics (automated):
- Plan question diversity (embedding cosine distance)
- Sub-report confidence distribution
- Replan trigger rate
- Token usage vs budget
- Citation coverage
- End-to-end latency

Rubric-based LLM judge (manual/weekly):
- Structure, evidence quality, coverage, synthesis, coherence, citation accuracy (1-5)
"""
from __future__ import annotations

import math
import pytest
from typing import Any


def question_diversity_score(questions: list[str]) -> float:
    """Compute average pairwise cosine distance of question embeddings.
    Higher = more diverse. Returns 0 if < 2 questions.
    Placeholder: in production, compute actual embeddings."""
    if len(questions) < 2:
        return 0.0
    return 0.7  # Placeholder


def confidence_distribution(confidences: list[float]) -> dict[str, float]:
    """Stats on sub-report confidence scores."""
    if not confidences:
        return {"mean": 0, "min": 0, "max": 0, "std": 0}
    mean = sum(confidences) / len(confidences)
    variance = sum((c - mean) ** 2 for c in confidences) / len(confidences)
    return {
        "mean": round(mean, 3),
        "min": round(min(confidences), 3),
        "max": round(max(confidences), 3),
        "std": round(math.sqrt(variance), 3),
    }


def citation_coverage(sections: list[dict], total_sources: int) -> float:
    """Fraction of cited sources out of total available."""
    if total_sources == 0:
        return 1.0
    cited = set()
    for sec in sections:
        cited.update(sec.get("source_ids_used", []))
    return len(cited) / total_sources


def token_efficiency(used_tokens: int, budget: int) -> float:
    """Ratio of tokens used vs budget. <1 means under budget."""
    if budget == 0:
        return 0.0
    return round(used_tokens / budget, 3)


class TestDRMetrics:
    def test_diversity_single(self):
        assert question_diversity_score(["q1"]) == 0.0

    def test_confidence_distribution_empty(self):
        assert confidence_distribution([])["mean"] == 0

    def test_citation_coverage_no_sources(self):
        assert citation_coverage([], 0) == 1.0

    def test_token_efficiency(self):
        assert token_efficiency(50_000, 200_000) == 0.25
