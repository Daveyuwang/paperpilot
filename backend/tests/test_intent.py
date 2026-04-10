"""Unit tests for the intent classifier."""
import asyncio
import pytest
from unittest.mock import patch, AsyncMock

from app.agents.intent import (
    classify_intent,
    _keyword_pre_filter,
    is_intent_routing_enabled,
    intent_to_scope_label,
)


# ── Keyword pre-filter: external_expansion ────────────────────────────────

class TestKeywordPreFilterExpansion:
    def test_latest_papers(self):
        assert _keyword_pre_filter("What are the latest papers on this topic?") == "external_expansion"

    def test_recent_papers(self):
        assert _keyword_pre_filter("Show me recent papers about transformers") == "external_expansion"

    def test_beyond_paper(self):
        assert _keyword_pre_filter("What work exists beyond this paper?") == "external_expansion"

    def test_follow_up_work(self):
        assert _keyword_pre_filter("Any follow-up work on this approach?") == "external_expansion"

    def test_state_of_the_art(self):
        assert _keyword_pre_filter("What is the state of the art?") == "external_expansion"

    def test_chinese_latest_work(self):
        # Exact phrase from the bug report
        assert _keyword_pre_filter("帮我找下关于这篇的最新工作") == "external_expansion"

    def test_literature_review(self):
        assert _keyword_pre_filter("Do a literature review on this topic") == "external_expansion"

    def test_find_papers(self):
        assert _keyword_pre_filter("find related papers on UDF optimization") == "external_expansion"

    def test_search_papers(self):
        assert _keyword_pre_filter("search for papers on this topic") == "external_expansion"

    def test_further_reading(self):
        assert _keyword_pre_filter("Any further reading recommendations?") == "external_expansion"

    def test_more_recent_work(self):
        assert _keyword_pre_filter("Is there more recent work on this method?") == "external_expansion"

    def test_chinese_latest(self):
        assert _keyword_pre_filter("帮我找下关于这篇的最新工作") == "external_expansion"

    def test_chinese_related(self):
        assert _keyword_pre_filter("有没有相关论文可以参考") == "external_expansion"

    def test_chinese_find(self):
        assert _keyword_pre_filter("帮我查找一下最新的相关研究") == "external_expansion"


# ── Keyword pre-filter: navigation_or_next_step ───────────────────────────

class TestKeywordPreFilterNavigation:
    def test_what_should_read_next(self):
        assert _keyword_pre_filter("What should I read next?") == "navigation_or_next_step"

    def test_where_go_next(self):
        assert _keyword_pre_filter("Where should I go next in my learning?") == "navigation_or_next_step"

    def test_next_step(self):
        assert _keyword_pre_filter("What's the next step?") == "navigation_or_next_step"

    def test_go_deeper(self):
        assert _keyword_pre_filter("How do I go deeper on this topic?") == "navigation_or_next_step"

    def test_learning_path(self):
        assert _keyword_pre_filter("Give me a learning path for this area") == "navigation_or_next_step"

    def test_chinese_continue(self):
        assert _keyword_pre_filter("接下来我应该读什么") == "navigation_or_next_step"

    def test_chinese_next_step(self):
        assert _keyword_pre_filter("下一步该怎么做") == "navigation_or_next_step"


# ── Keyword pre-filter: no match ─────────────────────────────────────────

class TestKeywordPreFilterNoMatch:
    def test_no_match_results_question(self):
        # Paper-specific questions with no keyword triggers → LLM path
        assert _keyword_pre_filter("What accuracy did the model achieve?") is None

    def test_no_match_comparison(self):
        assert _keyword_pre_filter("How does this approach perform compared to baselines?") is None

    def test_no_match_paper_anchor_blocks_concept(self):
        # "what is" would trigger concept_explanation, but "in this paper" is a paper anchor → None
        assert _keyword_pre_filter("What is the method described in this paper?") is None


# ── Feature flag ──────────────────────────────────────────────────────────

class TestFeatureFlag:
    def test_disabled(self):
        with patch("app.agents.intent.settings") as mock_settings:
            mock_settings.enable_intent_routing = False
            assert not is_intent_routing_enabled()

    def test_enabled(self):
        with patch("app.agents.intent.settings") as mock_settings:
            mock_settings.enable_intent_routing = True
            assert is_intent_routing_enabled()

    def test_default_is_true(self):
        # The config default is True — routing should be on out of the box
        from app.config import get_settings
        # get_settings is cached; check the field directly
        from app.config import Settings
        assert Settings.__fields__["enable_intent_routing"].default is True


# ── classify_intent when flag is off ──────────────────────────────────────

class TestClassifyIntentFlagOff:
    def test_defaults_to_paper_understanding(self):
        with patch("app.agents.intent.settings") as mock_settings:
            mock_settings.enable_intent_routing = False
            intent, conf = asyncio.get_event_loop().run_until_complete(
                classify_intent("What are the latest papers?", "Test Paper")
            )
            assert intent == "paper_understanding"
            assert conf == 1.0


# ── classify_intent keyword path (flag on) ───────────────────────────────

class TestClassifyIntentKeyword:
    def test_expansion_via_keyword_english(self):
        with patch("app.agents.intent.settings") as mock_settings:
            mock_settings.enable_intent_routing = True
            intent, conf = asyncio.get_event_loop().run_until_complete(
                classify_intent("What are the latest related papers?", "Test Paper")
            )
            assert intent == "external_expansion"
            assert conf == 0.9

    def test_navigation_via_keyword(self):
        with patch("app.agents.intent.settings") as mock_settings:
            mock_settings.enable_intent_routing = True
            intent, conf = asyncio.get_event_loop().run_until_complete(
                classify_intent("What should I read next?", "Test Paper")
            )
            assert intent == "navigation_or_next_step"
            assert conf == 0.9

    def test_chinese_expansion_via_keyword(self):
        with patch("app.agents.intent.settings") as mock_settings:
            mock_settings.enable_intent_routing = True
            intent, conf = asyncio.get_event_loop().run_until_complete(
                classify_intent("帮我找下关于这篇的最新工作", "UDFBench")
            )
            assert intent == "external_expansion"
            assert conf == 0.9


# ── intent_to_scope_label ─────────────────────────────────────────────────

class TestScopeLabel:
    def test_paper_understanding(self):
        assert intent_to_scope_label("paper_understanding") == "Using this paper"

    def test_external_expansion(self):
        assert intent_to_scope_label("external_expansion") == "Beyond this paper"

    def test_expansion_alias(self):
        assert intent_to_scope_label("expansion") == "Beyond this paper"

    def test_concept_explanation(self):
        assert "paper context" in intent_to_scope_label("concept_explanation").lower()

    def test_navigation(self):
        assert intent_to_scope_label("navigation_or_next_step") == "Your learning path"

    def test_ambiguous_falls_back(self):
        assert intent_to_scope_label("ambiguous") == "Using this paper"

    def test_unknown_falls_back(self):
        assert intent_to_scope_label("totally_unknown") == "Using this paper"
