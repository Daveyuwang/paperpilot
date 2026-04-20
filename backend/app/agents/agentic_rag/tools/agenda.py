"""
Agenda tools for the Console agent.

Note: Agenda items are currently managed in frontend state (no ORM model).
These tools return instructions for the frontend to execute.
"""
from __future__ import annotations

from langchain_core.tools import tool


@tool
async def get_agenda(workspace_id: str) -> dict:
    """Get current agenda items with their status.

    Use this tool when the user asks 'what should I do next', 'what's my plan',
    'what's on my list', or wants to see their research agenda.

    Note: Agenda is managed in frontend state. This returns a query instruction.

    Args:
        workspace_id: The workspace ID.
    """
    return {
        "action": "query_agenda",
        "workspace_id": workspace_id,
        "instruction": "Frontend should provide the current agenda items with status.",
        "note": "Agenda data is managed client-side. The agent should use conversation context or ask the user about their current priorities.",
    }


@tool
async def update_agenda(
    workspace_id: str,
    action: str,
    item_description: str = "",
    item_id: str = "",
    priority: str = "medium",
) -> dict:
    """Add, complete, or update agenda items.

    Use this tool when the user asks to add tasks, mark something done, or reprioritize.

    Args:
        workspace_id: The workspace ID.
        action: One of 'add', 'complete', 'update'.
        item_description: Description of the agenda item (required for 'add').
        item_id: ID of existing item (required for 'complete' and 'update').
        priority: Priority level: 'high', 'medium', 'low' (for 'add' and 'update').
    """
    if action == "add" and not item_description:
        return {"error": "item_description is required for 'add' action."}
    if action in ("complete", "update") and not item_id:
        return {"error": f"item_id is required for '{action}' action."}

    return {
        "action": f"agenda_{action}",
        "workspace_id": workspace_id,
        "item_id": item_id,
        "item_description": item_description,
        "priority": priority,
        "instruction": f"Frontend should {action} the agenda item.",
    }
