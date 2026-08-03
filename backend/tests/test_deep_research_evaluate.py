"""Focused tests for the pre-synthesis LLM evidence evaluator."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

import pytest

from app.deep_research.models import (
    EvidenceIssue,
    EvidenceRepairDirective,
    PreSynthesisEvaluation,
    PreSynthesisEvaluationRun,
    PreSynthesisScores,
    SourceRef,
    SubQuestion,
    SubReport,
)
from app.deep_research.nodes import evaluate as evaluate_module


class _FakeStructuredLLM:
    def __init__(self, response: Any = None, error: BaseException | None = None):
        self.response = response
        self.error = error
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any) -> Any:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.response


def _sub_question(question_id: str) -> SubQuestion:
    return SubQuestion(
        id=question_id,
        question=f"What is known about {question_id}?",
        search_queries=[f"{question_id} primary evidence"],
        priority=1,
        rationale="Required for the research objective.",
    )


def _sub_report(question_id: str) -> SubReport:
    return SubReport(
        sub_question_id=question_id,
        question=f"What is known about {question_id}?",
        findings=f"Retained evidence finding for {question_id}.",
        key_facts=[f"Material fact for {question_id}."],
        confidence=0.8,
        gaps="No independent excerpts were retained.",
        sources=[
            SourceRef(
                url=f"https://evidence.example/{question_id}",
                title=f"Primary source for {question_id}",
            )
        ],
    )


def _scores() -> PreSynthesisScores:
    return PreSynthesisScores(
        intent_alignment=90,
        must_answer_coverage=90,
        source_relevance=90,
        source_quality=75,
        source_diversity=75,
        source_recency=75,
        grounding_consistency=75,
        contradiction_handling=75,
        synthesis_readiness=90,
    )


def _evaluation(
    *,
    assessed_ids: list[str] | None = None,
    issues: list[EvidenceIssue] | None = None,
    directives: list[EvidenceRepairDirective] | None = None,
) -> PreSynthesisEvaluation:
    return PreSynthesisEvaluation(
        schema_version="pre-synthesis-evaluation.v1",
        rubric_version="pre-synthesis-rubric.v1",
        assessed_sub_question_ids=assessed_ids or ["sq-a", "sq-b"],
        scores=_scores(),
        issues=issues or [],
        repair_directives=directives or [],
        unresolved_questions=[],
        evaluation_limitations=[
            "Fetched source excerpts are not retained, so grounding cannot be independently verified."
        ],
        summary="The retained corpus is ready for synthesis.",
    )


def _state() -> dict[str, Any]:
    return {
        "topic": "A test research objective",
        "depth": "standard",
        "api_key": "SUPER-SECRET-EVALUATOR-KEY",
        "llm_base_url": "https://api.deepseek.com/v1",
        "llm_model": "deepseek-reasoner",
        "sub_questions": [_sub_question("sq-a"), _sub_question("sq-b")],
        "sub_reports": [_sub_report("sq-a"), _sub_report("sq-b")],
        "failed_queries": [],
    }


def _patch_llm(
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeStructuredLLM,
) -> list[dict[str, Any]]:
    factory_calls: list[dict[str, Any]] = []

    def fake_factory(state, schema, **kwargs):
        factory_calls.append({"state": state, "schema": schema, "kwargs": kwargs})
        return fake

    monkeypatch.setattr(evaluate_module, "make_structured_llm", fake_factory)
    return factory_calls


@pytest.mark.asyncio
async def test_valid_evaluation_invokes_llm_and_stores_completed_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeStructuredLLM(response=_evaluation())
    factory_calls = _patch_llm(monkeypatch, fake)

    update = await evaluate_module.evidence_evaluate_node(_state())

    assert "sub_reports" not in update
    run = update["pre_synthesis_evaluation_run"]
    assert isinstance(run, PreSynthesisEvaluationRun)
    assert run.status == "completed"
    assert run.error_code is None
    assert run.evaluation == _evaluation()
    assert run.evaluator_model == "deepseek-reasoner"
    assert run.attempts == 1
    assert run.duration_ms >= 0
    assert len(factory_calls) == 1
    assert factory_calls[0]["schema"] is PreSynthesisEvaluation
    assert factory_calls[0]["kwargs"]["temperature"] == 0.0
    assert fake.calls

    prompt = json.dumps(fake.calls, default=str, ensure_ascii=False)
    assert "SUPER-SECRET-EVALUATOR-KEY" not in prompt
    assert "Retained evidence finding for sq-a" in prompt
    assert "Material fact for sq-b" in prompt
    assert "https://evidence.example/sq-a" in prompt
    assert "sq-a" in prompt and "sq-b" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_key",
    ["sub_questions", "sub_reports"],
)
async def test_missing_inputs_fail_without_calling_llm(
    monkeypatch: pytest.MonkeyPatch,
    missing_key: str,
) -> None:
    state = _state()
    state[missing_key] = []
    fake = _FakeStructuredLLM(response=_evaluation())
    factory_calls = _patch_llm(monkeypatch, fake)

    update = await evaluate_module.evidence_evaluate_node(state)

    run = update["pre_synthesis_evaluation_run"]
    assert run.status == "failed"
    assert run.evaluation is None
    assert run.error_code == "missing_inputs"
    assert run.evaluator_model == "deepseek-reasoner"
    assert run.attempts == 0
    assert factory_calls == []
    assert fake.calls == []
    assert "sub_reports" not in update


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("assessed_ids", "expected_code"),
    [
        (["sq-a"], "invalid_references"),
        (["sq-a", "sq-b", "sq-hallucinated"], "invalid_references"),
        (["sq-a", "sq-a"], "invalid_output"),
    ],
)
async def test_invalid_assessed_ids_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    assessed_ids: list[str],
    expected_code: str,
) -> None:
    payload = _evaluation().model_dump()
    payload["assessed_sub_question_ids"] = assessed_ids
    fake = _FakeStructuredLLM(response=payload)
    _patch_llm(monkeypatch, fake)

    update = await evaluate_module.evidence_evaluate_node(_state())

    run = update["pre_synthesis_evaluation_run"]
    assert run.status == "failed"
    assert run.evaluation is None
    assert run.error_code == expected_code
    assert "sub_reports" not in update


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("issue_target_ids", "issue_urls", "directive_target_ids"),
    [
        (["sq-hallucinated"], ["https://evidence.example/sq-a"], ["sq-a"]),
        (["sq-a"], ["https://hallucinated.example/source"], ["sq-a"]),
        (["sq-a"], ["https://evidence.example/sq-a"], ["sq-hallucinated"]),
    ],
)
async def test_hallucinated_issue_or_directive_references_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    issue_target_ids: list[str],
    issue_urls: list[str],
    directive_target_ids: list[str],
) -> None:
    issue = EvidenceIssue(
        id="issue-1",
        category="grounding_gap",
        severity="major",
        description="More evidence is required.",
        affected_sub_question_ids=issue_target_ids,
        source_urls=issue_urls,
    )
    directive = EvidenceRepairDirective(
        id="repair-1",
        issue_ids=["issue-1"],
        target_sub_question_ids=directive_target_ids,
        objective="Resolve the evidence gap.",
        suggested_queries=["primary evidence for the material claim"],
        acceptance_criteria=["At least one retained primary source supports the claim."],
    )
    fake = _FakeStructuredLLM(
        response=_evaluation(issues=[issue], directives=[directive])
    )
    _patch_llm(monkeypatch, fake)

    update = await evaluate_module.evidence_evaluate_node(_state())

    run = update["pre_synthesis_evaluation_run"]
    assert run.status == "failed"
    assert run.evaluation is None
    assert run.error_code == "invalid_references"


def _invalid_payload(case: str) -> dict[str, Any]:
    payload = _evaluation().model_dump()
    issue = {
        "id": "issue-1",
        "category": "coverage_gap",
        "severity": "major",
        "description": "A required branch is missing.",
        "affected_sub_question_ids": ["sq-a"],
        "source_urls": ["https://evidence.example/sq-a"],
    }
    directive = {
        "id": "repair-1",
        "issue_ids": ["issue-1"],
        "target_sub_question_ids": ["sq-a"],
        "objective": "Fill the missing branch.",
        "suggested_queries": ["missing branch primary source"],
        "acceptance_criteria": ["The missing branch has primary evidence."],
    }
    if case == "duplicate_issue_ids":
        payload["issues"] = [deepcopy(issue), deepcopy(issue)]
        payload["repair_directives"] = [directive]
    elif case == "duplicate_directive_ids":
        payload["issues"] = [issue]
        payload["repair_directives"] = [deepcopy(directive), deepcopy(directive)]
    elif case == "uncovered_major_issue":
        payload["issues"] = [issue]
        payload["repair_directives"] = []
    elif case == "uncovered_blocker_issue":
        issue["severity"] = "blocker"
        payload["issues"] = [issue]
        payload["repair_directives"] = []
    else:  # pragma: no cover - protects additions to the test table
        raise AssertionError(f"unknown invalid payload case: {case}")
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "duplicate_issue_ids",
        "duplicate_directive_ids",
        "uncovered_major_issue",
        "uncovered_blocker_issue",
    ],
)
async def test_schema_invariants_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    # Returning a raw payload models a structured-output parser handing an
    # invalid object to the node. The node must validate it rather than trust it.
    fake = _FakeStructuredLLM(response=_invalid_payload(case))
    _patch_llm(monkeypatch, fake)

    update = await evaluate_module.evidence_evaluate_node(_state())

    run = update["pre_synthesis_evaluation_run"]
    assert run.status == "failed"
    assert run.evaluation is None
    assert run.error_code == "invalid_output"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error", "expected_code"),
    [
        (None, asyncio.TimeoutError(), "timeout"),
        (None, RuntimeError("provider unavailable"), "provider_error"),
        ({"schema_version": "not-the-schema"}, None, "invalid_output"),
    ],
)
async def test_evaluator_failures_overwrite_stale_success(
    monkeypatch: pytest.MonkeyPatch,
    response: Any,
    error: BaseException | None,
    expected_code: str,
) -> None:
    state = _state()
    state["pre_synthesis_evaluation_run"] = PreSynthesisEvaluationRun(
        status="completed",
        evaluation=_evaluation(),
        error_code=None,
        evaluator_model="deepseek-reasoner",
        attempts=1,
        duration_ms=123,
    )
    fake = _FakeStructuredLLM(response=response, error=error)
    _patch_llm(monkeypatch, fake)

    update = await evaluate_module.evidence_evaluate_node(state)

    run = update["pre_synthesis_evaluation_run"]
    assert run.status == "failed"
    assert run.evaluation is None
    assert run.error_code == expected_code
    assert run.evaluator_model == "deepseek-reasoner"
    assert run.attempts == 2
    assert run.duration_ms >= 0
    assert run != state["pre_synthesis_evaluation_run"]
    assert "sub_reports" not in update
