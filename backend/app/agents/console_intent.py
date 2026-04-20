"""
Workspace console intent classifier.

Intent classes (priority order):
  1. deliverable_edit    — explicit deliverable/section reference, rewrite/revise/expand
  2. deliverable_draft   — draft new content for a deliverable
  3. discover_sources    — find/collect/add papers or sources
  4. compare_sources     — compare methods/papers/sources
  5. paper_question      — about the active paper (if one is set)
  6. workspace_question  — general research topic question
  7. agenda_followup     — follow up on agenda item
  8. navigation_or_open  — open paper, switch tab, navigate
"""
from __future__ import annotations

import re
import structlog

logger = structlog.get_logger()

CONSOLE_INTENTS = {
    "deliverable_edit",
    "deliverable_draft",
    "discover_sources",
    "compare_sources",
    "paper_question",
    "workspace_question",
    "agenda_followup",
    "navigation_or_open",
}

_DELIVERABLE_EDIT_KEYWORDS = re.compile(
    r"\b(rewrite|revise|expand|shorten|improve|edit|update|rephrase|rework)\b"
    r"|\b(section|deliverable|draft|paragraph|introduction|conclusion|related work)\b",
    re.IGNORECASE,
)

_DISCOVER_KEYWORDS = re.compile(
    r"\b(find|search|discover|collect|add|look for|get)\s+.{0,20}"
    r"(papers?|sources?|articles?|references?|literature)\b"
    r"|帮我(?:找|查找|搜索).*?(?:论文|文献|来源)",
    re.IGNORECASE,
)

_COMPARE_KEYWORDS = re.compile(
    r"\b(compare|contrast|difference|similarities|versus|vs\.?)\b"
    r"|比较|对比|区别",
    re.IGNORECASE,
)

_NAVIGATION_KEYWORDS = re.compile(
    r"\b(open|show|switch to|go to|navigate)\b",
    re.IGNORECASE,
)


def classify_console_intent(
    message: str,
    has_active_paper: bool = False,
    has_active_deliverable: bool = False,
    has_focused_section: bool = False,
) -> tuple[str, float]:
    """
    Rule-based console intent classification.
    Returns (intent, confidence).
    """
    # Priority 1: deliverable edit (explicit section/deliverable reference + edit verb)
    if has_focused_section and _DELIVERABLE_EDIT_KEYWORDS.search(message):
        return ("deliverable_edit", 0.85)

    # Priority 2: deliverable draft
    if has_active_deliverable and re.search(r"\b(draft|write|generate|create)\b", message, re.IGNORECASE):
        if re.search(r"\b(section|content|paragraph|outline)\b", message, re.IGNORECASE):
            return ("deliverable_draft", 0.8)

    # Priority 3: discover sources
    if _DISCOVER_KEYWORDS.search(message):
        return ("discover_sources", 0.85)

    # Priority 4: compare sources
    if _COMPARE_KEYWORDS.search(message):
        return ("compare_sources", 0.8)

    # Priority 5: paper question (if active paper)
    if has_active_paper:
        paper_anchors = re.search(
            r"\b(this paper|the paper|the authors?|the method|the model|"
            r"the approach|the experiment|the result|figure|table)\b",
            message, re.IGNORECASE,
        )
        if paper_anchors:
            return ("paper_question", 0.8)

    # Priority 6: navigation
    if _NAVIGATION_KEYWORDS.search(message):
        return ("navigation_or_open", 0.7)

    # Default: workspace question
    return ("workspace_question", 0.6)


def console_intent_to_scope_label(intent: str) -> str:
    return {
        "deliverable_edit": "Editing deliverable",
        "deliverable_draft": "Drafting content",
        "discover_sources": "Discovering sources",
        "compare_sources": "Comparing sources",
        "paper_question": "Using active paper",
        "workspace_question": "Workspace research",
        "agenda_followup": "Following up",
        "navigation_or_open": "Navigating",
    }.get(intent, "Workspace research")
