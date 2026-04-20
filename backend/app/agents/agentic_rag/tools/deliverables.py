"""
Deliverable tools for the Console agent.
Wraps the existing draft generation pipeline.

Note: Deliverables are currently managed in frontend state (no ORM model).
These tools interface with the draft API and return instructions for the frontend.
"""
from __future__ import annotations

from langchain_core.tools import tool


@tool
async def list_deliverables(workspace_id: str) -> dict:
    """List all deliverables in the workspace with their section completion status.

    Use this tool when the user asks about their documents, what needs to be done,
    or wants a progress overview of their writing.

    Note: Deliverables are managed in frontend state. This tool returns a message
    instructing the frontend to provide the deliverable list.

    Args:
        workspace_id: The workspace ID.
    """
    # Deliverables live in frontend state — return a frontend query instruction
    return {
        "action": "query_deliverables",
        "workspace_id": workspace_id,
        "instruction": "Frontend should provide the current deliverable list with section status.",
        "note": "Deliverable data is managed client-side. The agent should ask the user about their deliverables or use context from the conversation.",
    }


@tool
async def read_deliverable_section(deliverable_id: str, section_id: str) -> dict:
    """Read the content of a specific deliverable section.

    Use this tool when the user references a specific section and wants to review
    what's written, or when you need to understand existing content before suggesting changes.

    Args:
        deliverable_id: The deliverable ID.
        section_id: The section ID within the deliverable.
    """
    return {
        "action": "read_section",
        "deliverable_id": deliverable_id,
        "section_id": section_id,
        "instruction": "Frontend should provide the content of this section.",
    }


@tool
async def draft_deliverable_section(
    deliverable_id: str,
    section_id: str,
    deliverable_title: str,
    section_title: str,
    deliverable_type: str = "notes",
    instructions: str = "",
) -> dict:
    """Trigger AI draft generation for a deliverable section.

    Use this tool when the user asks to write, fill, or draft content for a section.
    This initiates the draft pipeline — actual generation happens asynchronously via SSE.

    Args:
        deliverable_id: The deliverable ID.
        section_id: The section ID to draft.
        deliverable_title: Title of the deliverable document.
        section_title: Title of the section to draft.
        deliverable_type: Type of deliverable ('deep_research', 'proposal', 'research_plan', 'notes').
        instructions: Optional specific instructions for the draft content.
    """
    return {
        "action": "trigger_draft",
        "deliverable_id": deliverable_id,
        "section_id": section_id,
        "deliverable_title": deliverable_title,
        "section_title": section_title,
        "deliverable_type": deliverable_type,
        "instructions": instructions,
        "instruction": (
            f"Frontend should trigger the draft SSE endpoint for section '{section_title}' "
            f"in deliverable '{deliverable_title}'. "
            f"The draft pipeline will generate content using workspace sources."
        ),
    }
