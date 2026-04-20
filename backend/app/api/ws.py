import json
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from datetime import datetime

from app.agents.graph import run_agent_turn
from app.agents.console import run_console_turn
from app.db.postgres import AsyncSessionLocal
from app.models.orm import Session

logger = structlog.get_logger()
router = APIRouter()


@router.websocket("/chat/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for streaming Q&A.

    Client sends: {"question": "...", "question_id": "optional-guide-q-id"}
    Server streams: token/chunk_refs/answer_done/next_question/error messages
    """
    guest_id = (websocket.query_params.get("guest_id") or "").strip()
    if not guest_id:
        await websocket.close(code=4400, reason="Missing guest_id.")
        return

    async with AsyncSessionLocal() as db:
        session = await db.get(Session, session_id)
        if not session or session.guest_id != guest_id:
            await websocket.close(code=4404, reason="Session not found.")
            return
        session.last_active = datetime.utcnow()
        await db.commit()
        is_console_session = session.paper_id is None
        workspace_id = session.workspace_id

    await websocket.accept()
    logger.info("ws_connected", session_id=session_id, guest_id=guest_id, is_console=is_console_session)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "content": "Invalid JSON."})
                continue

            question = data.get("question", "").strip()
            if not question:
                await websocket.send_json({"type": "error", "content": "Empty question."})
                continue

            question_id = data.get("question_id")
            mode_override = data.get("mode_override") or None
            context = data.get("context") or {}

            if is_console_session:
                async for message in run_console_turn(
                    session_id=session_id,
                    workspace_id=workspace_id or "",
                    question=question,
                    guest_id=guest_id,
                    context=context,
                ):
                    await websocket.send_json(message)
            else:
                async for message in run_agent_turn(
                    session_id,
                    question,
                    question_id,
                    mode_override=mode_override,
                    guest_id=guest_id,
                ):
                    await websocket.send_json(message)

    except WebSocketDisconnect:
        logger.info("ws_disconnected", session_id=session_id, guest_id=guest_id)
    except Exception as exc:
        logger.exception("ws_error", session_id=session_id, guest_id=guest_id, error=str(exc))
        try:
            await websocket.send_json({"type": "error", "content": "Internal server error."})
        except Exception:
            pass
