"""
Lightweight intent classifier for question routing.

Intents:
  paper_understanding     — questions about the paper's specific content
  paper_navigation        — requests to find specific sections/figures
  concept_explanation     — asking to explain a term, concept, or method
  external_expansion      — requests beyond the paper (related work, latest papers, etc.)
  navigation_or_next_step — asking what to read/study next; learning path guidance
  ambiguous               — classifier is uncertain; falls back to paper_understanding
  unsupported             — clearly out of scope

Feature flag: ENABLE_INTENT_ROUTING (default True).
When off, all questions default to paper_understanding.
"""
from __future__ import annotations

import json
import re
import structlog
from anthropic import AsyncAnthropic
from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

VALID_INTENTS = {
    "paper_understanding",
    "paper_navigation",
    "concept_explanation",
    "external_expansion",
    "navigation_or_next_step",
    "ambiguous",          # LLM may emit this; route → paper_understanding
    "unsupported",
    # backward compat alias — treated as external_expansion
    "expansion",
}

# Keywords that strongly suggest external_expansion intent
_EXPANSION_KEYWORDS = re.compile(
    # English — explicit external-search signals
    r"\b(latest|recent papers?|other papers?|beyond this paper|state of the art|"
    r"follow[- ]up work|related work beyond|compare with other|"
    r"newer (methods?|approaches?|papers?)|outside this (paper|study)|"
    r"survey|literature review|prior work outside|further reading|"
    r"broader literature|in the (?:field|area|community)|"
    r"published (?:after|since|beyond|later)|more recent (?:work|paper|method)|"
    r"(?:find|search|look)\s+(?:for\s+)?(?:related\s+)?(?:papers?|research|work|methods?))\b"
    # Chinese — no \b needed for CJK
    r"|最新(?:进展|研究|论文|工作)|相关(?:工作|论文|研究)|"
    r"帮我(?:找|查找|搜索)|推荐.*?论文|更新的.*?(?:方法|工作)",
    re.IGNORECASE,
)

# Keywords that suggest navigation/next-step intent
_NAVIGATION_KEYWORDS = re.compile(
    r"\b(what should I (?:read|study|explore) next|where (?:should I|to) go next|"
    r"next (?:step|topic|thing|paper)|go (?:deeper|further)|"
    r"what(?:'s| is) next|guide me|where to (?:start|continue|go)|"
    r"what else should I|learning path|reading order|"
    r"how (?:should I|do I) continue)\b"
    r"|继续(?:读|学|讲)|下一步|接下来|该学什么|怎么继续|推荐下一篇",
    re.IGNORECASE,
)

# Keywords that suggest concept_explanation intent
# "what is X", "explain X", "define X", "meaning of X", "how does X work"
_CONCEPT_KEYWORDS = re.compile(
    r"\b(what is|what are|explain|define|definition of|meaning of|"
    r"how does .{1,40} work|how do .{1,40} work|what does .{3,30} mean|"
    r"describe|overview of|background on)\b",
    re.IGNORECASE,
)

# Phrases that indicate the user is asking about the paper itself (not general concepts)
_PAPER_ANCHOR_KEYWORDS = re.compile(
    r"\b(this paper|the paper|the authors?|the study|the method|the model|"
    r"the approach|the experiment|the result|the contribution|"
    r"in this work|according to|as described|as proposed|the proposed)\b",
    re.IGNORECASE,
)

INTENT_SYSTEM_PROMPT = """You are an intent classifier for a paper reading assistant.
Given a user question and the paper title, classify the intent into exactly one of:
- paper_understanding: questions about THIS paper's specific content (its methods, results,
  contributions, experimental setup, limitations, figures, tables)
- paper_navigation: asking to locate a specific section, figure, table, or equation IN this paper
- concept_explanation: asking to explain a general concept or term — even if the concept
  appears in the paper, the question seeks a universal explanation, not what the paper says
- external_expansion: requesting information beyond the paper — related work, comparisons
  with other papers, latest advances, field overviews, finding additional resources
- navigation_or_next_step: asking for guidance on what to read, study, or explore next;
  learning path recommendations; meta-questions about progression
- unsupported: clearly unrelated to academic reading

CRITICAL DISTINCTIONS:
- "What is [technique]?" → concept_explanation (general knowledge request)
- "How does [technique] work in this paper?" → paper_understanding (paper-specific)
- "What are other approaches to [topic]?" → external_expansion (seeks external resources)
- "What should I read next?" → navigation_or_next_step
- A question about the paper's TOPIC does NOT default to paper_understanding if the
  user is asking generally or seeking external comparison.

Respond with JSON only — no extra text:
{"intent": "<intent>", "confidence": <float 0-1>, "reason_code": "<paper_specific|general_concept|external_request|navigation|off_topic>"}"""


def is_intent_routing_enabled() -> bool:
    return settings.enable_intent_routing


def _keyword_pre_filter(question: str) -> str | None:
    """Rule-based fast path. Returns intent string or None."""
    # External expansion takes priority
    if _EXPANSION_KEYWORDS.search(question):
        return "external_expansion"

    # Navigation/next-step
    if _NAVIGATION_KEYWORDS.search(question):
        return "navigation_or_next_step"

    # Concept explanation only if there's no paper anchor (not asking about this specific paper)
    if _CONCEPT_KEYWORDS.search(question) and not _PAPER_ANCHOR_KEYWORDS.search(question):
        return "concept_explanation"

    return None


def _normalize_intent(raw: str) -> str:
    """Normalize and backward-compat map intent strings."""
    raw = raw.strip().lower()
    if raw == "expansion":
        return "external_expansion"
    return raw if raw in VALID_INTENTS else "paper_understanding"


async def classify_intent(
    question: str,
    paper_title: str,
) -> tuple[str, float]:
    """
    Classify the intent of a question.
    Returns (intent, confidence).
    """
    if not is_intent_routing_enabled():
        return ("paper_understanding", 1.0)

    # Rule-based pre-filter (fast path)
    keyword_match = _keyword_pre_filter(question)
    if keyword_match:
        logger.info("intent_classified", intent=keyword_match, confidence=0.9, method="keyword")
        return (keyword_match, 0.9)

    # LLM-based classification
    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        resp = await client.messages.create(
            model=settings.claude_model,
            max_tokens=100,
            system=INTENT_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Paper: {paper_title}\nQuestion: {question}",
            }],
        )
        text = resp.content[0].text.strip()
        data = json.loads(text)
        intent = _normalize_intent(data.get("intent", "paper_understanding"))
        confidence = float(data.get("confidence", 0.5))
        reason_code = data.get("reason_code", "")

        # "ambiguous" → conservative fallback
        if intent == "ambiguous":
            intent = "paper_understanding"

        # Low confidence falls back to paper_understanding
        if confidence < 0.6:
            intent = "paper_understanding"

        logger.info("intent_classified", intent=intent, confidence=confidence,
                    reason_code=reason_code, method="llm")
        return (intent, confidence)

    except Exception as exc:
        logger.warning("intent_classification_failed", error=str(exc))
        return ("paper_understanding", 1.0)


def intent_to_scope_label(intent: str) -> str:
    """Return a human-readable scope label for the answer card badge."""
    return {
        "paper_understanding":     "Using this paper",
        "paper_navigation":        "Using this paper",
        "concept_explanation":     "General explanation with paper context",
        "external_expansion":      "Beyond this paper",
        "expansion":               "Beyond this paper",
        "navigation_or_next_step": "Your learning path",
        "ambiguous":               "Using this paper",
        "unsupported":             "Out of scope",
    }.get(intent, "Using this paper")
