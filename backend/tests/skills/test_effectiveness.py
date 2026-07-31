"""Behavioral acceptance tests for metadata-only dynamic skill routing.

The catalog used here is deliberately small and hermetic, but its names and
metadata mirror representative entries in Orchestra Research's upstream skill
repository.  These tests measure routing quality independently from network
refresh and prompt rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.skills.service import SkillService
from app.skills.source import SourceSnapshot, SourceStatus


@dataclass(frozen=True, slots=True)
class RoutingCase:
    label: str
    query: str
    flow: str
    expected_first: str | None
    allowed_names: tuple[str, ...] = ()


ROUTING_CASES = (
    RoutingCase(
        label="paper-qa-understanding-needs-no-writing-skill",
        query="Summarize this paper's main contribution and limitations.",
        flow="paper_qa",
        expected_first=None,
    ),
    RoutingCase(
        label="deep-research-vector-retrieval",
        query=(
            "Design high-performance FAISS HNSW vector similarity search for retrieval."
        ),
        flow="deep_research",
        expected_first="faiss",
        allowed_names=("faiss",),
    ),
    RoutingCase(
        label="proposal-research-ideation",
        query=(
            "Brainstorm novel research ideas and discover a high-impact problem "
            "direction for my proposal."
        ),
        flow="proposal",
        expected_first="brainstorming-research-ideas",
        allowed_names=("brainstorming-research-ideas",),
    ),
    RoutingCase(
        label="writing-ml-paper",
        query="Draft and revise a NeurIPS ML paper with verified citations.",
        flow="paper_qa",
        expected_first="ml-paper-writing",
        allowed_names=("ml-paper-writing",),
    ),
    RoutingCase(
        label="academic-publication-plotting",
        query="Create a publication-quality figure with Matplotlib for my ML paper.",
        flow="paper_qa",
        expected_first="academic-plotting",
        allowed_names=("academic-plotting",),
    ),
    RoutingCase(
        label="conference-talk-presentation",
        query="Prepare conference presentation slides and speaker notes from my paper.",
        flow="paper_qa",
        expected_first="presenting-conference-talks",
        allowed_names=("presenting-conference-talks",),
    ),
    RoutingCase(
        label="distributed-training-fsdp2",
        query="Add PyTorch FSDP2 fully_shard DTensor distributed training.",
        flow="deep_research",
        expected_first="pytorch-fsdp2",
        allowed_names=("pytorch-fsdp2", "deepspeed"),
    ),
    RoutingCase(
        label="evaluation-lm-harness",
        query=(
            "Benchmark LLM model quality on MMLU and HumanEval with LM evaluation "
            "harness."
        ),
        flow="deep_research",
        expected_first="evaluating-llms-harness",
        allowed_names=("evaluating-llms-harness", "nemo-evaluator"),
    ),
    RoutingCase(
        label="unrelated-no-skill",
        query="What time is tomorrow's team lunch?",
        flow="console",
        expected_first=None,
    ),
    RoutingCase(
        label="generic-machine-learning-no-skill",
        query="机器学习",
        flow="deep_research",
        expected_first=None,
    ),
    RoutingCase(
        label="ordinary-email-writing-no-skill",
        query="Write an email to my team",
        flow="console",
        expected_first=None,
    ),
    RoutingCase(
        label="ordinary-meeting-agenda-no-skill",
        query="Write a meeting agenda",
        flow="console",
        expected_first=None,
    ),
    RoutingCase(
        label="production-help-is-not-an-exact-tag-query",
        query="Can you help with production?",
        flow="deep_research",
        expected_first=None,
    ),
    RoutingCase(
        label="ordinary-python-script-no-skill",
        query="Write a Python script to process PDFs.",
        flow="console",
        expected_first=None,
    ),
    RoutingCase(
        label="ordinary-meeting-edit-no-skill",
        query="Edit my meeting notes for clarity.",
        flow="console",
        expected_first=None,
    ),
    RoutingCase(
        label="research-code-writing-no-skill",
        query="Write research code to run model experiments.",
        flow="console",
        expected_first=None,
    ),
    RoutingCase(
        label="research-plan-drafting-no-skill",
        query="Draft a research plan for data collection.",
        flow="console",
        expected_first=None,
    ),
    RoutingCase(
        label="academic-email-writing-no-skill",
        query="Write an academic email to my advisor.",
        flow="console",
        expected_first=None,
    ),
    RoutingCase(
        label="gguf-model-quantization",
        query="quantize a model with GGUF",
        flow="deep_research",
        expected_first="gguf-quantization",
        allowed_names=("gguf-quantization",),
    ),
    RoutingCase(
        label="systems-paper-explicit-venue",
        query="Draft an OSDI systems paper about storage architecture.",
        flow="paper_qa",
        expected_first="systems-paper-writing",
        allowed_names=("systems-paper-writing",),
    ),
    RoutingCase(
        label="chinese-academic-paper-writing",
        query="帮我撰写一篇机器学习论文并检查引用",
        flow="paper_qa",
        expected_first="ml-paper-writing",
        allowed_names=("ml-paper-writing",),
    ),
)


def _activate_effectiveness_catalog(
    root: Path,
    write_skill,
    *,
    max_selected: int = 2,
) -> SkillService:
    write_skill(
        root,
        "08-distributed-training/pytorch-fsdp2",
        name="pytorch-fsdp2",
        description=(
            "Add PyTorch FSDP2 fully_shard with DTensor sharding, mixed precision, "
            "and distributed checkpointing"
        ),
        tags=("PyTorch", "FSDP2", "Distributed Training", "DTensor"),
        category="distributed-training",
        body="FSDP2-BODY-SENTINEL\n",
    )
    write_skill(
        root,
        "08-distributed-training/deepspeed",
        name="deepspeed",
        description="Distributed training with DeepSpeed ZeRO and pipeline parallelism",
        tags=("DeepSpeed", "Distributed Training", "ZeRO"),
        category="distributed-training",
        body="DEEPSPEED-BODY-SENTINEL\n",
    )
    write_skill(
        root,
        "11-evaluation/lm-evaluation-harness",
        name="evaluating-llms-harness",
        description=(
            "Evaluate LLM model quality across academic benchmarks including MMLU "
            "and HumanEval"
        ),
        tags=("Evaluation", "LM Evaluation Harness", "MMLU", "HumanEval"),
        category="evaluation",
        body="EVALUATION-BODY-SENTINEL\n",
    )
    write_skill(
        root,
        "11-evaluation/nemo-evaluator",
        name="nemo-evaluator",
        description="Evaluate generative AI models with reproducible benchmarks",
        tags=("Evaluation", "Benchmarking", "Model Quality"),
        category="evaluation",
        body="NEMO-EVALUATOR-BODY-SENTINEL\n",
    )
    write_skill(
        root,
        "15-rag/faiss",
        name="faiss",
        description=(
            "High-performance vector similarity search using FAISS, HNSW, and GPU "
            "acceleration"
        ),
        tags=("RAG", "FAISS", "HNSW", "Vector Search", "Retrieval"),
        category="rag",
        body="FAISS-BODY-SENTINEL\n",
    )
    write_skill(
        root,
        "20-ml-paper-writing/ml-paper-writing",
        name="ml-paper-writing",
        description=(
            "Write publication-ready ML papers for NeurIPS, draft arguments, and "
            "verify citations"
        ),
        tags=("Academic Writing", "NeurIPS", "ML", "Paper", "Citations"),
        category="ml-paper-writing",
        body="ML-WRITING-BODY-SENTINEL\n",
    )
    write_skill(
        root,
        "20-ml-paper-writing/academic-plotting",
        name="academic-plotting",
        description=(
            "Generates publication-quality figures for ML papers from research "
            "context with matplotlib and seaborn"
        ),
        tags=(
            "Academic Writing",
            "Visualization",
            "Matplotlib",
            "Seaborn",
            "Plotting",
            "Figures",
            "Diagrams",
            "NeurIPS",
        ),
        category="ml-paper-writing",
        body="ACADEMIC-PLOTTING-BODY-SENTINEL\n",
    )
    write_skill(
        root,
        "20-ml-paper-writing/presenting-conference-talks",
        name="presenting-conference-talks",
        description=(
            "Generates conference presentation slides with speaker notes and a "
            "talk script from a compiled paper"
        ),
        tags=(
            "Presenting Conference Talks",
            "Beamer",
            "PPTX",
            "Slides",
            "Speaker Notes",
            "NeurIPS",
        ),
        category="ml-paper-writing",
        body="CONFERENCE-TALK-BODY-SENTINEL\n",
    )
    write_skill(
        root,
        "21-research-ideation/brainstorming-research-ideas",
        name="brainstorming-research-ideas",
        description=(
            "Brainstorm novel high-impact research ideas, discover problems, and "
            "develop proposal directions"
        ),
        tags=("Research Ideation", "Brainstorming", "Problem Discovery"),
        category="research-ideation",
        body="IDEATION-BODY-SENTINEL\n",
    )
    write_skill(
        root,
        "06-model-optimization/gguf-quantization",
        name="gguf-quantization",
        description=(
            "GGUF format and llama.cpp quantization for efficient model inference"
        ),
        tags=("GGUF", "Quantization", "llama.cpp", "Production"),
        category="model-optimization",
        body="LLAMA-CPP-BODY-SENTINEL\n",
    )
    write_skill(
        root,
        "19-systems-paper-writing/systems-paper-writing",
        name="systems-paper-writing",
        description=(
            "Write publication-ready computer systems papers for OSDI and SOSP"
        ),
        tags=("Academic Writing", "OSDI", "SOSP", "Systems Paper"),
        # Upstream currently groups this named skill under ml-paper-writing.
        # Name precedence keeps the specialized systems route unambiguous.
        category="ml-paper-writing",
        body="SYSTEMS-WRITING-BODY-SENTINEL\n",
    )
    write_skill(
        root,
        "17-safety-alignment/constitutional-ai",
        name="constitutional-ai",
        description="Align AI assistants with constitutional principles",
        tags=("AI Safety", "Alignment"),
        category="safety-alignment",
        body="CONSTITUTIONAL-AI-BODY-SENTINEL\n",
    )

    settings = Settings(
        agent_skills_enabled=True,
        agent_skills_cache_dir=str(root / "unused-cache"),
        agent_skills_max_selected=max_selected,
        agent_skills_max_prompt_chars=4_000,
        agent_skills_min_score=6.0,
    )
    service = SkillService(settings)
    service._activate(
        SourceSnapshot(
            root=root,
            revision="e" * 40,
            status=SourceStatus.CACHED,
            refreshed_at=1.0,
            source_url="fixture://effectiveness-catalog",
            ref="fixture",
        )
    )
    return service


@pytest.mark.parametrize(
    "case",
    ROUTING_CASES,
    ids=lambda case: case.label,
)
def test_labeled_queries_route_relevant_top_k_without_false_activation(
    tmp_path: Path,
    write_skill,
    case: RoutingCase,
) -> None:
    service = _activate_effectiveness_catalog(tmp_path, write_skill)

    selected = service.select(case.query, flow=case.flow)

    assert len(selected.names) <= service.settings.agent_skills_max_selected
    assert len(selected.names) == len(set(selected.names))
    assert set(selected.names).issubset(case.allowed_names)
    if case.expected_first is None:
        assert selected.names == ()
    else:
        assert selected.names
        assert selected.names[0] == case.expected_first


def test_broad_query_never_exceeds_configured_selection_budget(
    tmp_path: Path,
    write_skill,
) -> None:
    service = _activate_effectiveness_catalog(tmp_path, write_skill, max_selected=2)

    selected = service.select(
        (
            "Compare PyTorch FSDP2 and DeepSpeed distributed training, then evaluate "
            "model quality with MMLU benchmarks and LM evaluation harness."
        ),
        flow="deep_research",
    )

    assert len(selected.names) == 2
    assert len(selected.scores) == 2


def test_preview_routes_metadata_without_loading_selected_bodies(
    tmp_path: Path,
    write_skill,
    monkeypatch,
) -> None:
    from app.api import skills as skills_api

    service = _activate_effectiveness_catalog(tmp_path, write_skill)
    counter_keys = (
        "loaded_count",
        "loaded_bytes",
        "loaded_reference_count",
        "cache_entry_count",
        "cache_total_bytes",
        "cache_hits",
        "cache_misses",
        "cache_evictions",
    )
    before = service.status()
    monkeypatch.setattr(skills_api, "get_skill_service", lambda: service)

    def unexpected_body_load(*_args, **_kwargs) -> None:
        raise AssertionError("preview attempted to load a skill body")

    monkeypatch.setattr(
        "app.skills.registry.load_verified_skill_body",
        unexpected_body_load,
    )
    app = FastAPI()
    app.include_router(skills_api.router, prefix="/api/skills")

    response = TestClient(app).post(
        "/api/skills/preview",
        json={
            "query": "Use FAISS HNSW for high-performance vector retrieval",
            "flow": "deep_research",
            "max_results": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available_count"] == 12
    assert payload["loaded_count"] == 0
    assert payload["selected_count"] == 1
    assert payload["selected"][0]["name"] == "faiss"
    assert payload["selected"][0]["loaded"] is False
    assert payload["selected"][0]["matched_terms"]
    assert "FAISS-BODY-SENTINEL" not in repr(payload)
    assert payload["cache"]["loaded_count"] == 0
    assert payload["cache"]["loaded_bytes"] == 0

    after = service.status()
    assert {key: after[key] for key in counter_keys} == {
        key: before[key] for key in counter_keys
    }
