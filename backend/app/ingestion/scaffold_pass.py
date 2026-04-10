"""
One-time scaffold pass: generates the guided question trail using Claude.
Called synchronously from the Celery worker after ingestion.
"""
from __future__ import annotations
import json
import re
import structlog
from anthropic import Anthropic

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

STAGE_LABELS = {
    "motivation": "motivation",
    "approach": "approach",
    "experiments": "experiments",
    "takeaways": "takeaways",
}

SCAFFOLD_SYSTEM = """You are an expert research assistant helping readers deeply understand academic papers.
Given a paper's title, abstract, and section headers, generate a structured guided question trail.
The trail must follow four stages:
  1. motivation (2-3 questions): problem, importance, prior work limitations
  2. approach (4-5 questions): core method, novelty, assumptions, design choices
  3. experiments (3-4 questions): evaluation setup, results, baselines, ablations
  4. takeaways (2-3 questions): contributions, limitations, future directions

Output a JSON array. Each item must have:
  - "question": string
  - "stage": one of motivation|approach|experiments|takeaways
  - "anchor_sections": list of section titles this question maps to (from the provided headers)

Respond ONLY with valid JSON, no markdown fences."""


def generate_question_trail(
    title: str,
    abstract: str,
    section_headers: list[str],
) -> list[dict]:
    """
    Call Claude to generate a 10-15 question guided trail.
    Returns a list of dicts: {question, stage, anchor_sections}.
    """
    client = Anthropic(api_key=settings.anthropic_api_key)

    user_content = f"""Title: {title or 'Unknown'}

Abstract:
{abstract or 'Not available'}

Section Headers:
{json.dumps(section_headers or [], indent=2)}

Generate the guided question trail."""

    try:
        message = client.messages.create(
            model=settings.claude_model,
            max_tokens=2048,
            system=SCAFFOLD_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = message.content[0].text.strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        questions = json.loads(raw)

        # Validate and normalize
        result = []
        for i, q in enumerate(questions):
            stage = q.get("stage", "motivation")
            if stage not in STAGE_LABELS:
                stage = "motivation"
            result.append({
                "question": q["question"],
                "stage": STAGE_LABELS[stage],
                "anchor_sections": q.get("anchor_sections", []),
            })

        logger.info("scaffold_questions_generated", count=len(result))
        return result

    except Exception as exc:
        logger.error("scaffold_pass_failed", error=str(exc))
        # Return a minimal fallback set
        return _fallback_questions(title)


def _fallback_questions(title: str) -> list[dict]:
    return [
        {"question": f"What problem does '{title}' address?", "stage": "motivation", "anchor_sections": []},
        {"question": "Why is this problem important and what are the limitations of prior work?", "stage": "motivation", "anchor_sections": []},
        {"question": "What is the core method or approach proposed?", "stage": "approach", "anchor_sections": []},
        {"question": "What is technically novel about this approach?", "stage": "approach", "anchor_sections": []},
        {"question": "What assumptions does the method make?", "stage": "approach", "anchor_sections": []},
        {"question": "How is the method evaluated?", "stage": "experiments", "anchor_sections": []},
        {"question": "What do the experimental results show?", "stage": "experiments", "anchor_sections": []},
        {"question": "What baselines are used for comparison?", "stage": "experiments", "anchor_sections": []},
        {"question": "What are the key contributions of this paper?", "stage": "takeaways", "anchor_sections": []},
        {"question": "What are the limitations and potential future directions?", "stage": "takeaways", "anchor_sections": []},
    ]
