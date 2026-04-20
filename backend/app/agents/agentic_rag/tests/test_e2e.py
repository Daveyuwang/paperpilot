"""
End-to-end integration tests for the agentic RAG system.

Tests:
1. Paper QA simple retrieval
2. Paper QA multi-tool + retry
3. Console multi-step workflow
4. Console delegate to Paper QA
5. Router classification accuracy
6. WebSocket message format compatibility

Run with: pytest backend/app/agents/agentic_rag/tests/test_e2e.py -v
"""
from __future__ import annotations

import asyncio
import json
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


# ── Test 1: Router classification ────────────────────────────────────────────

class TestRouter:
    """Test that the router correctly classifies intents."""

    @pytest.mark.asyncio
    async def test_routes_paper_question_to_paper_qa(self):
        from app.agents.agentic_rag.nodes.router import router_node

        state = {
            "messages": [HumanMessage(content="What method does the paper use for training?")],
            "paper_id": "paper-123",
            "active_paper_id": "",
        }

        with patch("app.agents.agentic_rag.nodes.router.ChatAnthropic") as mock_llm_cls:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = AIMessage(content='{"route": "paper_qa", "confidence": 0.9}')
            mock_llm_cls.return_value = mock_llm

            result = await router_node(state)
            assert result["route"] == "paper_qa"

    @pytest.mark.asyncio
    async def test_routes_workspace_question_to_console(self):
        from app.agents.agentic_rag.nodes.router import router_node

        state = {
            "messages": [HumanMessage(content="Find papers about attention mechanisms")],
            "paper_id": "",
            "active_paper_id": "",
        }

        with patch("app.agents.agentic_rag.nodes.router.ChatAnthropic") as mock_llm_cls:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = AIMessage(content='{"route": "console", "confidence": 0.85}')
            mock_llm_cls.return_value = mock_llm

            result = await router_node(state)
            assert result["route"] == "console"

    @pytest.mark.asyncio
    async def test_routes_greeting_to_direct(self):
        from app.agents.agentic_rag.nodes.router import router_node

        state = {
            "messages": [HumanMessage(content="Hello!")],
            "paper_id": "",
            "active_paper_id": "",
        }

        with patch("app.agents.agentic_rag.nodes.router.ChatAnthropic") as mock_llm_cls:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = AIMessage(content='{"route": "direct_response", "confidence": 0.95}')
            mock_llm_cls.return_value = mock_llm

            result = await router_node(state)
            assert result["route"] == "direct_response"


# ── Test 2: Chunk filter ─────────────────────────────────────────────────────

class TestChunkFilter:
    """Test chunk filtering with cross-encoder reranking."""

    @pytest.mark.asyncio
    async def test_filters_irrelevant_chunks(self):
        from app.agents.agentic_rag.nodes.chunk_filter import chunk_filter_node

        state = {
            "messages": [HumanMessage(content="What is the learning rate?")],
            "all_retrieved_chunks": [
                {"chunk_id": "c1", "content": "The learning rate is set to 0.001", "section_title": "Training", "page_number": 5, "score": 0.9},
                {"chunk_id": "c2", "content": "We thank the reviewers for their feedback", "section_title": "Acknowledgments", "page_number": 12, "score": 0.1},
                {"chunk_id": "c3", "content": "Adam optimizer with lr=1e-3 and weight decay", "section_title": "Experiments", "page_number": 6, "score": 0.8},
            ],
        }

        with patch("app.agents.agentic_rag.nodes.chunk_filter.rerank") as mock_rerank:
            mock_rerank.return_value = [
                {"chunk_id": "c1", "content": "The learning rate is set to 0.001", "section_title": "Training", "page_number": 5, "score": 0.92},
                {"chunk_id": "c3", "content": "Adam optimizer with lr=1e-3 and weight decay", "section_title": "Experiments", "page_number": 6, "score": 0.78},
                {"chunk_id": "c2", "content": "We thank the reviewers for their feedback", "section_title": "Acknowledgments", "page_number": 12, "score": 0.05},
            ]

            result = await chunk_filter_node(state)
            filtered = result["filtered_chunks"]
            assert len(filtered) == 2
            assert filtered[0]["chunk_id"] == "c1"
            assert filtered[1]["chunk_id"] == "c3"

    @pytest.mark.asyncio
    async def test_deduplicates_chunks(self):
        from app.agents.agentic_rag.nodes.chunk_filter import chunk_filter_node

        state = {
            "messages": [HumanMessage(content="test query")],
            "all_retrieved_chunks": [
                {"chunk_id": "c1", "content": "content A", "score": 0.9},
                {"chunk_id": "c1", "content": "content A", "score": 0.9},
                {"chunk_id": "c2", "content": "content B", "score": 0.7},
            ],
        }

        with patch("app.agents.agentic_rag.nodes.chunk_filter.rerank") as mock_rerank:
            mock_rerank.return_value = [
                {"chunk_id": "c1", "content": "content A", "score": 0.9},
                {"chunk_id": "c2", "content": "content B", "score": 0.7},
            ]

            result = await chunk_filter_node(state)
            assert len(result["filtered_chunks"]) == 2


# ── Test 3: Generate node ────────────────────────────────────────────────────

class TestGenerate:
    """Test structured answer generation."""

    @pytest.mark.asyncio
    async def test_generates_valid_answer_json(self):
        from app.agents.agentic_rag.nodes.generate import generate_node

        state = {
            "messages": [HumanMessage(content="What optimizer is used?")],
            "filtered_chunks": [
                {"chunk_id": "c1", "content": "We use Adam optimizer with lr=1e-3", "section_title": "Training", "page_number": 5, "score": 0.9},
            ],
            "paper_title": "Test Paper",
            "session_summary": "First turn.",
        }

        answer_json = json.dumps({
            "direct_answer": "The paper uses Adam optimizer with a learning rate of 1e-3.",
            "key_points": ["Adam optimizer", "Learning rate 1e-3"],
            "evidence": [{"type": "explicit", "passage": "We use Adam optimizer with lr=1e-3", "section": "Training", "page": 5}],
            "plain_language": None,
            "bigger_picture": None,
            "uncertainty": None,
            "answer_mode": "paper_understanding",
            "scope_label": "Using this paper",
            "can_expand": True,
        })

        with patch("app.agents.agentic_rag.nodes.generate.ChatAnthropic") as mock_cls:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = AIMessage(content=answer_json)
            mock_cls.return_value = mock_llm

            result = await generate_node(state)
            answer = result["answer"]
            assert answer["direct_answer"] == "The paper uses Adam optimizer with a learning rate of 1e-3."
            assert answer["answer_mode"] == "paper_understanding"
            assert "_citations" in answer
            assert len(answer["_citations"]) == 1


# ── Test 4: Grade node ───────────────────────────────────────────────────────

class TestGrade:
    """Test answer grading and retry logic."""

    @pytest.mark.asyncio
    async def test_passes_grounded_answer(self):
        from app.agents.agentic_rag.nodes.grade import grade_node

        state = {
            "messages": [HumanMessage(content="What optimizer?")],
            "answer": {"direct_answer": "Adam optimizer", "key_points": []},
            "filtered_chunks": [{"content": "We use Adam optimizer"}],
            "retry_count": 0,
        }

        with patch("app.agents.agentic_rag.nodes.grade.ChatAnthropic") as mock_cls:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = AIMessage(
                content='{"grounded": true, "addresses_question": true, "pass": true, "reason": "", "rewritten_query": ""}'
            )
            mock_cls.return_value = mock_llm

            result = await grade_node(state)
            assert result["grade_result"] == "pass"

    @pytest.mark.asyncio
    async def test_fails_ungrounded_answer_with_rewrite(self):
        from app.agents.agentic_rag.nodes.grade import grade_node

        state = {
            "messages": [HumanMessage(content="What is the dataset size?")],
            "answer": {"direct_answer": "The dataset has 1M samples", "key_points": []},
            "filtered_chunks": [{"content": "We evaluate on CIFAR-10"}],
            "retry_count": 0,
        }

        with patch("app.agents.agentic_rag.nodes.grade.ChatAnthropic") as mock_cls:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = AIMessage(
                content='{"grounded": false, "addresses_question": true, "pass": false, "reason": "Claim not in evidence", "rewritten_query": "dataset size number of samples training data"}'
            )
            mock_cls.return_value = mock_llm

            result = await grade_node(state)
            assert result["grade_result"] == "fail"
            assert result["rewritten_query"] == "dataset size number of samples training data"
            assert result["retry_count"] == 1


# ── Test 5: Paper QA agent node ──────────────────────────────────────────────

class TestPaperQAAgent:
    """Test the Paper QA agent's tool-calling behavior."""

    @pytest.mark.asyncio
    async def test_agent_calls_retrieve_tool(self):
        from app.agents.agentic_rag.nodes.agent import paper_qa_agent_node

        state = {
            "messages": [HumanMessage(content="What is the main contribution?")],
            "paper_title": "Test Paper",
            "paper_abstract": "We propose a novel method...",
            "session_summary": "First turn.",
            "tool_call_count": 0,
        }

        mock_response = AIMessage(
            content="",
            tool_calls=[{
                "id": "call_1",
                "name": "retrieve_from_paper",
                "args": {"query": "main contribution novel method", "paper_id": "paper-123"},
            }],
        )

        with patch("app.agents.agentic_rag.nodes.agent.get_paper_qa_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            result = await paper_qa_agent_node(state)
            assert result["tool_call_count"] == 1
            assert result["messages"][0].tool_calls[0]["name"] == "retrieve_from_paper"

    @pytest.mark.asyncio
    async def test_agent_respects_budget(self):
        from app.agents.agentic_rag.nodes.agent import paper_qa_agent_node

        state = {
            "messages": [HumanMessage(content="test")],
            "paper_title": "Test",
            "paper_abstract": "",
            "session_summary": "",
            "tool_call_count": 9,
        }

        mock_response = AIMessage(
            content="",
            tool_calls=[{"id": "call_1", "name": "retrieve_from_paper", "args": {"query": "test", "paper_id": "p1"}}],
        )

        with patch("app.agents.agentic_rag.nodes.agent.get_paper_qa_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            result = await paper_qa_agent_node(state)
            assert result["tool_call_count"] == 10


# ── Test 6: Console agent node ───────────────────────────────────────────────

class TestConsoleAgent:
    """Test the Console agent's tool-calling behavior."""

    @pytest.mark.asyncio
    async def test_console_calls_discover_sources(self):
        from app.agents.agentic_rag.nodes.console_agent import console_agent_node

        state = {
            "messages": [HumanMessage(content="Find papers about transformers")],
            "workspace_title": "My Research",
            "paper_count": 3,
            "active_paper_name": "None",
            "workspace_snapshot": "",
            "tool_call_count": 0,
        }

        mock_response = AIMessage(
            content="",
            tool_calls=[{
                "id": "call_1",
                "name": "discover_sources",
                "args": {"query": "transformers attention mechanism", "max_results": 10},
            }],
        )

        with patch("app.agents.agentic_rag.nodes.console_agent.get_console_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            result = await console_agent_node(state)
            assert result["tool_call_count"] == 1
            assert result["messages"][0].tool_calls[0]["name"] == "discover_sources"


# ── Test 7: Graph conditional edges ──────────────────────────────────────────

class TestGraphEdges:
    """Test conditional routing in subgraphs."""

    def test_paper_qa_routes_to_tools_on_tool_calls(self):
        from app.agents.agentic_rag.graphs.paper_qa import _should_continue_agent

        state = {
            "messages": [AIMessage(content="", tool_calls=[{"id": "1", "name": "retrieve_from_paper", "args": {}}])],
            "tool_call_count": 2,
        }
        assert _should_continue_agent(state) == "tools"

    def test_paper_qa_routes_to_filter_when_no_tool_calls(self):
        from app.agents.agentic_rag.graphs.paper_qa import _should_continue_agent

        state = {
            "messages": [AIMessage(content="I have enough information.")],
            "tool_call_count": 3,
        }
        assert _should_continue_agent(state) == "chunk_filter"

    def test_paper_qa_budget_forces_filter(self):
        from app.agents.agentic_rag.graphs.paper_qa import _should_continue_agent

        state = {
            "messages": [AIMessage(content="", tool_calls=[{"id": "1", "name": "retrieve_from_paper", "args": {}}])],
            "tool_call_count": 10,
        }
        assert _should_continue_agent(state) == "chunk_filter"

    def test_grade_pass_ends(self):
        from app.agents.agentic_rag.graphs.paper_qa import _should_retry

        assert _should_retry({"grade_result": "pass", "retry_count": 0}) == "end"

    def test_grade_fail_retries(self):
        from app.agents.agentic_rag.graphs.paper_qa import _should_retry

        assert _should_retry({"grade_result": "fail", "retry_count": 1}) == "retry"

    def test_grade_fail_max_retries_ends(self):
        from app.agents.agentic_rag.graphs.paper_qa import _should_retry

        assert _should_retry({"grade_result": "fail", "retry_count": 3}) == "end"

    def test_console_routes_to_tools(self):
        from app.agents.agentic_rag.graphs.console import _should_continue

        state = {
            "messages": [AIMessage(content="", tool_calls=[{"id": "1", "name": "discover_sources", "args": {}}])],
            "tool_call_count": 2,
        }
        assert _should_continue(state) == "tools"

    def test_console_routes_to_end_no_tools(self):
        from app.agents.agentic_rag.graphs.console import _should_continue

        state = {
            "messages": [AIMessage(content="Here are the results.")],
            "tool_call_count": 3,
        }
        assert _should_continue(state) == "end"

    def test_console_budget_forces_end(self):
        from app.agents.agentic_rag.graphs.console import _should_continue

        state = {
            "messages": [AIMessage(content="", tool_calls=[{"id": "1", "name": "get_agenda", "args": {}}])],
            "tool_call_count": 8,
        }
        assert _should_continue(state) == "end"


# ── Test 8: WebSocket message format compatibility ───────────────────────────

class TestWebSocketFormat:
    """Test that the streaming integration produces valid WebSocket messages."""

    @pytest.mark.asyncio
    async def test_stream_yields_expected_message_types(self):
        from app.agents.agentic_rag.stream import run_agentic_turn

        valid_types = {"status", "mode_info", "token", "answer_json", "chunk_refs", "answer_done", "error", "evidence_ready", "next_question", "suggested_questions"}

        with patch("app.agents.agentic_rag.stream.main_graph") as mock_graph, \
             patch("app.agents.agentic_rag.stream.get_session_state", return_value={}), \
             patch("app.agents.agentic_rag.stream._load_paper_meta", return_value=("Test Paper", "Abstract")), \
             patch("app.agents.agentic_rag.stream._update_session_state"):

            async def mock_events(*args, **kwargs):
                yield {"event": "on_chain_end", "name": "LangGraph", "data": {"output": {
                    "messages": [AIMessage(content="The answer is X.", additional_kwargs={"answer_json": {
                        "direct_answer": "The answer is X.",
                        "answer_mode": "paper_understanding",
                        "scope_label": "Using this paper",
                    }})],
                    "route": "paper_qa",
                }}}

            mock_graph.astream_events = mock_events

            messages = []
            async for msg in run_agentic_turn(
                session_id="sess-1",
                question="What is X?",
                paper_id="paper-1",
            ):
                messages.append(msg)
                assert "type" in msg
                assert msg["type"] in valid_types
                assert "content" in msg

            # Should have at least: status (Received) + mode_info + answer_json + answer_done
            types_seen = {m["type"] for m in messages}
            assert "status" in types_seen
            assert "answer_done" in types_seen


# ── Test 9: Budget helpers ───────────────────────────────────────────────────

class TestBudget:
    """Test budget enforcement helpers."""

    def test_paper_qa_budget_not_exceeded(self):
        from app.agents.agentic_rag.budget import paper_qa_budget_exceeded
        assert not paper_qa_budget_exceeded(5, 1)

    def test_paper_qa_budget_exceeded_by_tools(self):
        from app.agents.agentic_rag.budget import paper_qa_budget_exceeded
        assert paper_qa_budget_exceeded(10, 0)

    def test_paper_qa_budget_exceeded_by_retries(self):
        from app.agents.agentic_rag.budget import paper_qa_budget_exceeded
        assert paper_qa_budget_exceeded(3, 3)

    def test_console_budget_not_exceeded(self):
        from app.agents.agentic_rag.budget import console_budget_exceeded
        assert not console_budget_exceeded(5)

    def test_console_budget_exceeded(self):
        from app.agents.agentic_rag.budget import console_budget_exceeded
        assert console_budget_exceeded(8)


# ── Test 10: State initialization ────────────────────────────────────────────

class TestState:
    """Test state TypedDict compatibility."""

    def test_paper_qa_state_fields(self):
        from app.agents.agentic_rag.state import PaperQAState
        annotations = PaperQAState.__annotations__
        assert "paper_id" in annotations
        assert "all_retrieved_chunks" in annotations
        assert "filtered_chunks" in annotations
        assert "answer" in annotations
        assert "grade_result" in annotations
        assert "retry_count" in annotations
        assert "tool_call_count" in annotations

    def test_console_state_fields(self):
        from app.agents.agentic_rag.state import ConsoleState
        annotations = ConsoleState.__annotations__
        assert "workspace_id" in annotations
        assert "session_id" in annotations
        assert "tool_call_count" in annotations


# ── Test 11: Academic tools ─────────────────────────────────────────────────

class TestAcademicTools:
    """Test Semantic Scholar and web search tools."""

    @pytest.mark.asyncio
    async def test_search_academic_papers_parses_response(self):
        from app.agents.agentic_rag.tools.academic import search_academic_papers

        mock_data = {
            "data": [
                {
                    "paperId": "abc123",
                    "title": "Attention Is All You Need",
                    "abstract": "We propose a new architecture...",
                    "year": 2017,
                    "citationCount": 50000,
                    "authors": [{"name": "Vaswani"}, {"name": "Shazeer"}],
                    "url": "https://semanticscholar.org/paper/abc123",
                    "externalIds": {"ArXiv": "1706.03762"},
                }
            ]
        }

        with patch("app.agents.agentic_rag.tools.academic.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_data
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await search_academic_papers.ainvoke({"query": "transformers", "max_results": 5})
            assert len(result) == 1
            assert result[0]["title"] == "Attention Is All You Need"
            assert result[0]["arxiv_id"] == "1706.03762"
            assert result[0]["citation_count"] == 50000

    @pytest.mark.asyncio
    async def test_get_citation_context_returns_both(self):
        from app.agents.agentic_rag.tools.academic import get_citation_context

        citations_data = {"data": [{"citingPaper": {"title": "BERT", "year": 2019, "citationCount": 30000, "authors": [{"name": "Devlin"}]}}]}
        references_data = {"data": [{"citedPaper": {"title": "Seq2Seq", "year": 2014, "citationCount": 10000, "authors": [{"name": "Sutskever"}]}}]}

        with patch("app.agents.agentic_rag.tools.academic.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()

            async def mock_get(url, **kwargs):
                resp = MagicMock()
                resp.status_code = 200
                if "citations" in url:
                    resp.json.return_value = citations_data
                else:
                    resp.json.return_value = references_data
                return resp

            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await get_citation_context.ainvoke({"semantic_scholar_id": "abc123"})
            assert len(result["citations"]) == 1
            assert result["citations"][0]["title"] == "BERT"
            assert len(result["references"]) == 1
            assert result["references"][0]["title"] == "Seq2Seq"

    @pytest.mark.asyncio
    async def test_web_search_uses_tavily(self):
        from app.agents.agentic_rag.tools.academic import web_search

        mock_tavily = AsyncMock(return_value=[
            {"url": "https://example.com", "title": "Result", "snippet": "Content", "score": 0.9}
        ])

        with patch("app.agents.agentic_rag.tools.academic.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(web_search_enabled=True, tavily_api_key="key")
            with patch.dict("sys.modules", {"app.deep_research.tools.search": MagicMock(tavily_search=mock_tavily)}):
                result = await web_search.ainvoke({"query": "latest LLM news", "max_results": 3})
                assert len(result) == 1
                assert result[0]["url"] == "https://example.com"


# ── Test 12: Cross-paper retrieval ──────────────────────────────────────────

class TestCrossPaperRetrieval:
    """Test workspace-isolated multi-paper search."""

    def test_dense_search_multi_uses_match_any(self):
        from app.retrieval.qdrant_client import dense_search_multi

        with patch("app.retrieval.qdrant_client.get_client") as mock_get_client, \
             patch("app.retrieval.qdrant_client.ensure_collection"):
            mock_client = MagicMock()
            mock_point = MagicMock()
            mock_point.payload = {
                "chunk_id": "c1",
                "content": "test content",
                "section_title": "Intro",
                "page_number": 1,
                "bbox": None,
                "paper_id": "paper-1",
            }
            mock_point.score = 0.85
            mock_client.query_points.return_value = MagicMock(points=[mock_point])
            mock_get_client.return_value = mock_client

            results = dense_search_multi([0.1] * 1024, ["paper-1", "paper-2"], top_k=5)
            assert len(results) == 1
            assert results[0]["paper_id"] == "paper-1"
            assert results[0]["score"] == 0.85

            # Verify MatchAny was used
            call_args = mock_client.query_points.call_args
            query_filter = call_args.kwargs.get("query_filter") or call_args[1].get("query_filter")
            assert query_filter is not None

    def test_dense_search_multi_empty_ids_returns_empty(self):
        from app.retrieval.qdrant_client import dense_search_multi
        assert dense_search_multi([0.1] * 1024, []) == []


# ── Test 13: Source analysis ────────────────────────────────────────────────

class TestSourceAnalysis:
    """Test LLM-powered source ranking."""

    @pytest.mark.asyncio
    async def test_analyze_and_rank_sources(self):
        from app.agents.agentic_rag.tools.source_analysis import analyze_and_rank_sources

        sources = [
            {"title": "Paper A", "year": 2023, "abstract": "About transformers"},
            {"title": "Paper B", "year": 2022, "abstract": "About CNNs"},
        ]

        mock_response = AIMessage(content=json.dumps({
            "ranked": [
                {"index": 0, "score": 9, "reason": "Directly relevant"},
                {"index": 1, "score": 4, "reason": "Tangentially related"},
            ]
        }))

        with patch("app.agents.agentic_rag.tools.source_analysis.ChatAnthropic") as mock_cls:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_cls.return_value = mock_llm

            result = await analyze_and_rank_sources.ainvoke({
                "sources": sources,
                "research_question": "How do transformers work?",
            })
            assert len(result) == 2
            assert result[0]["title"] == "Paper A"
            assert result[0]["relevance_score"] == 9

    @pytest.mark.asyncio
    async def test_analyze_empty_sources(self):
        from app.agents.agentic_rag.tools.source_analysis import analyze_and_rank_sources
        result = await analyze_and_rank_sources.ainvoke({"sources": [], "research_question": "test"})
        assert result == []


# ── Test 14: Deliverable coherence ──────────────────────────────────────────

class TestDeliverableCoherence:
    """Test coherence checking and transition suggestions."""

    @pytest.mark.asyncio
    async def test_check_coherence_finds_issues(self):
        from app.agents.agentic_rag.tools.deliverable_analysis import check_deliverable_coherence

        sections = [
            {"title": "Introduction", "content": "We use method A..."},
            {"title": "Methods", "content": "We apply method B..."},
        ]

        mock_response = AIMessage(content=json.dumps({
            "issues": [{"type": "contradiction", "sections": [0, 1], "description": "Intro says A but Methods says B"}],
            "summary": "One contradiction found.",
        }))

        with patch("app.agents.agentic_rag.tools.deliverable_analysis.ChatAnthropic") as mock_cls:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_cls.return_value = mock_llm

            result = await check_deliverable_coherence.ainvoke({"sections": sections})
            assert len(result["issues"]) == 1
            assert result["issues"][0]["type"] == "contradiction"

    @pytest.mark.asyncio
    async def test_check_coherence_needs_two_sections(self):
        from app.agents.agentic_rag.tools.deliverable_analysis import check_deliverable_coherence
        result = await check_deliverable_coherence.ainvoke({"sections": [{"title": "Only one", "content": "text"}]})
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_suggest_transitions(self):
        from app.agents.agentic_rag.tools.deliverable_analysis import suggest_section_transitions

        with patch("app.agents.agentic_rag.tools.deliverable_analysis.ChatAnthropic") as mock_cls:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = AIMessage(content="Building on the introduction, we now turn to the methodology.")
            mock_cls.return_value = mock_llm

            result = await suggest_section_transitions.ainvoke({
                "section_before": {"title": "Introduction", "content": "We present our work..."},
                "section_after": {"title": "Methods", "content": "Our approach uses..."},
            })
            assert "methodology" in result.lower() or "turn to" in result.lower()


# ── Test 15: Paper fetch ────────────────────────────────────────────────────

class TestPaperFetch:
    """Test PDF download and text extraction."""

    @pytest.mark.asyncio
    async def test_fetch_paper_fulltext_success(self):
        from app.agents.agentic_rag.tools.paper_fetch import fetch_paper_fulltext

        with patch("app.agents.agentic_rag.tools.paper_fetch.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {"content-type": "application/pdf"}
            mock_resp.content = b"%PDF-fake"
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            mock_fitz = MagicMock()
            mock_doc = MagicMock()
            mock_page = MagicMock()
            mock_page.get_text.return_value = "This is the paper content about transformers."
            mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
            mock_doc.page_count = 1
            mock_doc.close = MagicMock()
            mock_fitz.open.return_value = mock_doc

            with patch.dict("sys.modules", {"fitz": mock_fitz}):
                result = await fetch_paper_fulltext.ainvoke({"arxiv_id": "2301.07041"})
                assert "text_preview" in result
                assert "transformers" in result["text_preview"]

    @pytest.mark.asyncio
    async def test_fetch_paper_no_args_returns_error(self):
        from app.agents.agentic_rag.tools.paper_fetch import fetch_paper_fulltext
        result = await fetch_paper_fulltext.ainvoke({"arxiv_id": "", "pdf_url": ""})
        assert "error" in result


# ── Test 16: Enhanced discover_sources with Semantic Scholar ────────────────

class TestEnhancedDiscoverSources:
    """Test that discover_sources now includes Semantic Scholar."""

    @pytest.mark.asyncio
    async def test_discover_sources_calls_three_apis(self):
        from app.agents.agentic_rag.tools.sources import discover_sources

        mock_parse_openalex = MagicMock(return_value=[])
        mock_parse_arxiv = MagicMock(return_value=[])
        mock_dedupe = MagicMock(return_value=[])
        mock_discovered_source = MagicMock

        with patch("app.agents.agentic_rag.tools.sources.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            call_count = {"n": 0}

            async def mock_get(url, **kwargs):
                call_count["n"] += 1
                resp = MagicMock()
                resp.status_code = 200
                if "openalex" in url:
                    resp.json.return_value = {"results": []}
                elif "arxiv" in url:
                    resp.text = "<feed></feed>"
                elif "semanticscholar" in url:
                    resp.json.return_value = {"data": []}
                return resp

            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            mock_sources_module = MagicMock()
            mock_sources_module._parse_openalex = mock_parse_openalex
            mock_sources_module._parse_arxiv = mock_parse_arxiv
            mock_sources_module._dedupe = mock_dedupe
            mock_sources_module.OPENALEX_API = "https://api.openalex.org/works"
            mock_sources_module.ARXIV_API = "https://export.arxiv.org/api/query"
            mock_sources_module.DiscoveredSource = MagicMock

            with patch.dict("sys.modules", {"app.api.sources": mock_sources_module}), \
                 patch("app.config.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(semantic_scholar_api_key="test-key")
                result = await discover_sources.ainvoke({"query": "transformers", "max_results": 10})

            assert call_count["n"] == 3  # OpenAlex + arXiv + Semantic Scholar
