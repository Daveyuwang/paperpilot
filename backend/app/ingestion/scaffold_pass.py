"""
One-time scaffold pass: generates the guided question trail using Claude.
Called synchronously from the Celery worker after ingestion.
"""
from __future__ import annotations
import json
import re
import structlog

from app.config import get_settings
from anthropic import Anthropic
import httpx

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


def _language_instruction(lang: str) -> str:
    lang = (lang or "en").strip()
    return {
        "en": "Write all questions in English.",
        "zh-CN": "Write all questions in Simplified Chinese.",
        "zh-TW": "Write all questions in Traditional Chinese.",
        "ja": "Write all questions in Japanese.",
        "ko": "Write all questions in Korean.",
        "es": "Write all questions in Spanish.",
        "fr": "Write all questions in French.",
        "de": "Write all questions in German.",
        "pt-BR": "Write all questions in Brazilian Portuguese.",
        "ru": "Write all questions in Russian.",
    }.get(lang, f"Write all questions in {lang}.")


def generate_question_trail(
    title: str,
    abstract: str,
    section_headers: list[str],
    *,
    guest_id: str = "",
    language: str = "en",
    protocol: str = "anthropic",
    base_url: str | None = None,
    api_key: str = "",
    model: str = "claude-sonnet-4-6",
) -> list[dict]:
    """
    Call Claude to generate a 10-15 question guided trail.
    Returns a list of dicts: {question, stage, anchor_sections}.
    """
    user_content = f"""Title: {title or 'Unknown'}

Abstract:
{abstract or 'Not available'}

Section Headers:
{json.dumps(section_headers or [], indent=2)}

Generate the guided question trail."""

    try:
        lang_note = _language_instruction(language) + "\n\n"
        system = lang_note + SCAFFOLD_SYSTEM

        raw = ""
        proto = (protocol or "anthropic").strip().lower()
        if proto == "anthropic":
            client = Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=model,
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            )
            raw = (resp.content[0].text or "").strip()
        elif proto in ("openai", "openai_compatible"):
            url_base = (base_url or "https://api.openai.com/v1").rstrip("/")
            url = url_base + "/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.2,
                "max_tokens": 2048,
                "stream": False,
            }
            with httpx.Client(timeout=60) as client:
                r = client.post(url, headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
                raw = (data["choices"][0]["message"]["content"] or "").strip()
        elif proto == "gemini":
            host = (base_url or "https://generativelanguage.googleapis.com").rstrip("/")
            url = f"{host}/v1beta/models/{model}:generateContent"
            params = {"key": api_key}
            payload = {
                "contents": [
                    {"role": "user", "parts": [{"text": system}]},
                    {"role": "user", "parts": [{"text": user_content}]},
                ],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2048},
            }
            with httpx.Client(timeout=60) as client:
                r = client.post(url, params=params, json=payload)
                r.raise_for_status()
                data = r.json()
                raw = (data["candidates"][0]["content"]["parts"][0].get("text") or "").strip()
        else:
            raise ValueError(f"Unsupported LLM protocol: {proto}")

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
