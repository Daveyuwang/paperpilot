"""
Chunk filter node — filters retrieved chunks for relevance using cross-encoder scores.
Runs after the agent finishes tool-calling, before generation.
"""
from __future__ import annotations

from app.agents.agentic_rag.config import CHUNK_RELEVANCE_THRESHOLD
from app.retrieval.reranker import rerank


async def chunk_filter_node(state: dict) -> dict:
    """Filter accumulated chunks for query relevance using cross-encoder reranking."""
    all_chunks = state.get("all_retrieved_chunks", [])
    if not all_chunks:
        return {"filtered_chunks": []}

    # Get the original user question from messages
    question = ""
    for msg in state["messages"]:
        if hasattr(msg, "type") and msg.type == "human":
            question = msg.content
            break

    if not question:
        return {"filtered_chunks": all_chunks[:5]}

    # Deduplicate by chunk_id
    seen = set()
    unique_chunks = []
    for c in all_chunks:
        cid = c.get("chunk_id", "")
        if cid and cid not in seen:
            seen.add(cid)
            unique_chunks.append(c)

    if not unique_chunks:
        return {"filtered_chunks": []}

    # Rerank with cross-encoder
    reranked = rerank(question, unique_chunks, top_k=min(len(unique_chunks), 8))

    # Filter by threshold
    filtered = [c for c in reranked if c.get("score", 0) >= CHUNK_RELEVANCE_THRESHOLD]

    # Ensure at least top 2 chunks pass through even if below threshold
    if len(filtered) < 2 and len(reranked) >= 2:
        filtered = reranked[:2]

    return {"filtered_chunks": filtered}
