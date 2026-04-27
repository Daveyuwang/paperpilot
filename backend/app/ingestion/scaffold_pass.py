"""
One-time scaffold pass: generates the guided question trail using an LLM.
Called synchronously from the Celery worker after ingestion.

The four stages (motivation, approach, experiments, takeaways) are called in
parallel via asyncio.to_thread so wall-clock time ≈ the slowest single call
instead of the sum of all four.
"""
from __future__ import annotations
import asyncio
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

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

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

STAGE_PROMPTS: dict[str, str] = {
    "motivation": (
        "You are an expert research assistant helping readers deeply understand academic papers.\n"
        "Given a paper's title, abstract, and section headers, generate 2-3 questions for the "
        "**motivation** stage ONLY.\n"
        "Focus on: the problem being addressed, why it is important, and the limitations of prior work.\n\n"
        "Output a JSON array. Each item must have:\n"
        '  - "question": string\n'
        '  - "stage": "motivation"\n'
        '  - "anchor_sections": list of section titles this question maps to (from the provided headers)\n\n'
        "Respond ONLY with valid JSON, no markdown fences."
    ),
    "approach": (
        "You are an expert research assistant helping readers deeply understand academic papers.\n"
        "Given a paper's title, abstract, and section headers, generate 4-5 questions for the "
        "**approach** stage ONLY.\n"
        "Focus on: the core method or technique, what is novel about it, key assumptions, and design choices.\n\n"
        "Output a JSON array. Each item must have:\n"
        '  - "question": string\n'
        '  - "stage": "approach"\n'
        '  - "anchor_sections": list of section titles this question maps to (from the provided headers)\n\n'
        "Respond ONLY with valid JSON, no markdown fences."
    ),
    "experiments": (
        "You are an expert research assistant helping readers deeply understand academic papers.\n"
        "Given a paper's title, abstract, and section headers, generate 3-4 questions for the "
        "**experiments** stage ONLY.\n"
        "Focus on: evaluation methodology, key results, baselines used for comparison, and ablation studies.\n\n"
        "Output a JSON array. Each item must have:\n"
        '  - "question": string\n'
        '  - "stage": "experiments"\n'
        '  - "anchor_sections": list of section titles this question maps to (from the provided headers)\n\n'
        "Respond ONLY with valid JSON, no markdown fences."
    ),
    "takeaways": (
        "You are an expert research assistant helping readers deeply understand academic papers.\n"
        "Given a paper's title, abstract, and section headers, generate 2-3 questions for the "
        "**takeaways** stage ONLY.\n"
        "Focus on: main contributions, acknowledged limitations, and suggested future research directions.\n\n"
        "Output a JSON array. Each item must have:\n"
        '  - "question": string\n'
        '  - "stage": "takeaways"\n'
        '  - "anchor_sections": list of section titles this question maps to (from the provided headers)\n\n'
        "Respond ONLY with valid JSON, no markdown fences."
    ),
}

# ---------------------------------------------------------------------------
# Helpers (unchanged)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# LLM call helper shared by both paths
# ---------------------------------------------------------------------------

def _raw_llm_call(
    system: str,
    user_content: str,
    *,
    protocol: str,
    base_url: str | None,
    api_key: str,
    model: str,
) -> str:
    """Make a single LLM call and return the raw text response."""
    proto = (protocol or "anthropic").strip().lower()

    if proto == "anthropic":
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        return (resp.content[0].text or "").strip()

    if proto in ("openai", "openai_compatible"):
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
        with httpx.Client(timeout=60) as http:
            r = http.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            return (data["choices"][0]["message"]["content"] or "").strip()

    if proto == "gemini":
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
        with httpx.Client(timeout=60) as http:
            r = http.post(url, params=params, json=payload)
            r.raise_for_status()
            data = r.json()
            return (data["candidates"][0]["content"]["parts"][0].get("text") or "").strip()

    raise ValueError(f"Unsupported LLM protocol: {proto}")


def _parse_llm_json(raw: str) -> list[dict]:
    """Strip markdown fences and parse JSON array from raw LLM output."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Single-call fallback (original logic, renamed)
# ---------------------------------------------------------------------------

def _generate_single_call(
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
    Single LLM call that generates all four stages at once.
    Kept as a synchronous fallback.
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

        raw = _raw_llm_call(
            system, user_content,
            protocol=protocol, base_url=base_url,
            api_key=api_key, model=model,
        )

        questions = _parse_llm_json(raw)

        result = []
        for q in questions:
            stage = q.get("stage", "motivation")
            if stage not in STAGE_LABELS:
                stage = "motivation"
            result.append({
                "question": q["question"],
                "stage": STAGE_LABELS[stage],
                "anchor_sections": q.get("anchor_sections", []),
            })

        logger.info("scaffold_questions_generated", count=len(result), mode="single_call")
        return result

    except Exception as exc:
        logger.error("scaffold_single_call_failed", error=str(exc))
        return _fallback_questions(title)


# ---------------------------------------------------------------------------
# Per-stage parallel call
# ---------------------------------------------------------------------------

def _call_llm_for_stage(
    stage: str,
    title: str,
    abstract: str,
    section_headers: list[str],
    *,
    language: str,
    protocol: str,
    base_url: str | None,
    api_key: str,
    model: str,
) -> list[dict]:
    """
    Make a single LLM call for one stage and return a list of dicts with
    {question, stage, anchor_sections}.
    """
    user_content = f"""Title: {title or 'Unknown'}

Abstract:
{abstract or 'Not available'}

Section Headers:
{json.dumps(section_headers or [], indent=2)}

Generate the questions for the {stage} stage."""

    lang_note = _language_instruction(language) + "\n\n"
    system = lang_note + STAGE_PROMPTS[stage]

    raw = _raw_llm_call(
        system, user_content,
        protocol=protocol, base_url=base_url,
        api_key=api_key, model=model,
    )

    questions = _parse_llm_json(raw)

    result = []
    for q in questions:
        result.append({
            "question": q["question"],
            "stage": stage,  # enforce correct stage regardless of LLM output
            "anchor_sections": q.get("anchor_sections", []),
        })

    logger.info("scaffold_stage_generated", stage=stage, count=len(result))
    return result


async def generate_question_trail_async(
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
    Async version: runs 4 stage LLM calls in parallel via asyncio.to_thread,
    then merges results into a flat list in stage order.
    Falls back to _fallback_questions on any error.
    """
    stages = ["motivation", "approach", "experiments", "takeaways"]

    try:
        tasks = [
            asyncio.to_thread(
                _call_llm_for_stage,
                stage,
                title,
                abstract,
                section_headers,
                language=language,
                protocol=protocol,
                base_url=base_url,
                api_key=api_key,
                model=model,
            )
            for stage in stages
        ]
        results = await asyncio.gather(*tasks)
        merged: list[dict] = []
        for stage_questions in results:
            merged.extend(stage_questions)

        logger.info("scaffold_questions_generated", count=len(merged), mode="parallel")
        return merged

    except Exception as exc:
        logger.error("scaffold_parallel_failed", error=str(exc))
        return _fallback_questions(title)


# ---------------------------------------------------------------------------
# Public entry point (synchronous, same signature as original)
# ---------------------------------------------------------------------------

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
    Generate a 10-15 question guided trail, with 4 stages called in parallel.
    Returns a list of dicts: {question, stage, anchor_sections}.
    Preserves the synchronous interface expected by the Celery worker.
    """
    try:
        return asyncio.run(
            generate_question_trail_async(
                title,
                abstract,
                section_headers,
                guest_id=guest_id,
                language=language,
                protocol=protocol,
                base_url=base_url,
                api_key=api_key,
                model=model,
            )
        )
    except RuntimeError:
        # If there is already a running event loop (e.g. Jupyter, some ASGI
        # servers), fall back to the synchronous single-call path.
        logger.warning("scaffold_asyncio_run_failed_using_single_call")
        return _generate_single_call(
            title,
            abstract,
            section_headers,
            guest_id=guest_id,
            language=language,
            protocol=protocol,
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
