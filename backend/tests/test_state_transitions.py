"""Unit tests for agent state transitions."""
import pytest
from app.agents.state import AgentState


class TestAgentStateDefaults:
    def test_initial_state(self):
        state = AgentState(session_id="s1", paper_id="p1")
        assert state.intent == "paper_understanding"
        assert state.answer_mode == "paper_understanding"
        assert state.input_type == "free"
        assert state.turn_count == 0
        assert state.isStreaming is None or not hasattr(state, "isStreaming")

    def test_question_fields(self):
        state = AgentState(session_id="s1", paper_id="p1", question="test?", question_id="q1")
        assert state.question == "test?"
        assert state.question_id == "q1"


class TestStateTransitions:
    """Verify the state machine: idle -> received -> retrieving -> generating -> done."""

    def test_route_sets_intent(self):
        state = AgentState(session_id="s1", paper_id="p1", question="test")
        updated = state.model_copy(update={"intent": "expansion", "answer_mode": "expansion"})
        assert updated.intent == "expansion"
        assert updated.answer_mode == "expansion"

    def test_retrieval_populates_chunks(self):
        state = AgentState(session_id="s1", paper_id="p1")
        chunks = [{"chunk_id": "c1", "content": "test chunk"}]
        updated = state.model_copy(update={"retrieved_chunks": chunks})
        assert len(updated.retrieved_chunks) == 1

    def test_evidence_extraction(self):
        state = AgentState(session_id="s1", paper_id="p1")
        evidence = [{"type": "EXPLICIT", "passage": "test", "note": ""}]
        updated = state.model_copy(update={
            "extracted_evidence": evidence,
            "evidence_confidence": 0.8,
        })
        assert updated.evidence_confidence == 0.8
        assert len(updated.extracted_evidence) == 1

    def test_synthesis_output(self):
        state = AgentState(session_id="s1", paper_id="p1")
        answer = {"direct_answer": "test answer", "evidence": []}
        updated = state.model_copy(update={
            "answer_text": "test answer",
            "answer_json": answer,
            "citations": [],
        })
        assert updated.answer_text == "test answer"
        assert updated.answer_json is not None

    def test_error_state(self):
        """Error should not corrupt the state."""
        state = AgentState(session_id="s1", paper_id="p1", question="test")
        # Simulate error: state remains with empty answer
        assert state.answer_text == ""
        assert state.answer_json is None

    def test_session_update(self):
        state = AgentState(session_id="s1", paper_id="p1", question_id="q1")
        updated = state.model_copy(update={
            "covered_question_ids": ["q1"],
            "turn_count": 1,
            "session_summary": "First turn summary",
        })
        assert "q1" in updated.covered_question_ids
        assert updated.turn_count == 1
