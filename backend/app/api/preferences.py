"""User preferences API — cross-workspace memory."""
import structlog
from datetime import datetime
from fastapi import APIRouter, Depends

from app.api.guest import require_guest_id
from app.db.postgres import AsyncSessionLocal
from app.models.orm import UserPreferences
from app.models.schemas import UserPreferencesOut, UserPreferencesUpdate

logger = structlog.get_logger()
router = APIRouter()


@router.get("/", response_model=UserPreferencesOut)
async def get_preferences(guest_id: str = Depends(require_guest_id)):
    async with AsyncSessionLocal() as db:
        prefs = await db.get(UserPreferences, guest_id)
        if not prefs:
            return UserPreferencesOut(guest_id=guest_id)
        return prefs


@router.put("/", response_model=UserPreferencesOut)
async def update_preferences(
    update: UserPreferencesUpdate,
    guest_id: str = Depends(require_guest_id),
):
    async with AsyncSessionLocal() as db:
        prefs = await db.get(UserPreferences, guest_id)
        if not prefs:
            prefs = UserPreferences(guest_id=guest_id)
            db.add(prefs)

        if update.terminology is not None:
            prefs.terminology = update.terminology
        if update.citation_style is not None:
            prefs.citation_style = update.citation_style
        if update.research_domains is not None:
            prefs.research_domains = update.research_domains
        if update.writing_style is not None:
            prefs.writing_style = update.writing_style
        if update.custom_instructions is not None:
            prefs.custom_instructions = update.custom_instructions
        prefs.updated_at = datetime.utcnow()

        await db.commit()
        await db.refresh(prefs)
    return prefs
