"""Shared pytest fixtures for evaluation tests."""
import json
import os
import pytest
from pathlib import Path

DATASETS_DIR = Path(__file__).parent / "datasets"


@pytest.fixture
def retrieval_dataset():
    path = DATASETS_DIR / "retrieval_eval_v1.json"
    if not path.exists():
        pytest.skip("retrieval_eval_v1.json dataset not found")
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def paper_qa_dataset():
    path = DATASETS_DIR / "paper_qa_eval_v1.json"
    if not path.exists():
        pytest.skip("paper_qa_eval_v1.json dataset not found")
    with open(path) as f:
        return json.load(f)
