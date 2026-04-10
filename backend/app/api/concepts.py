from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.guest import require_guest_id, get_owned_paper_or_404
from app.config import get_settings
from app.db.postgres import get_db
from app.models.orm import PaperConceptMap
from app.models.schemas import ConceptMapOut, ConceptNodeOut, ConceptEdgeOut

router = APIRouter()
settings = get_settings()


@router.get("/{paper_id}", response_model=ConceptMapOut)
async def get_concept_map(
    paper_id: str,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    """Return the LLM-generated concept map for a paper."""
    await get_owned_paper_or_404(db, paper_id, guest_id)
    record = await db.get(PaperConceptMap, paper_id)
    if not record or not record.data:
        return ConceptMapOut(nodes=[], edges=[], generated=False)

    data = record.data
    nodes = [ConceptNodeOut(**n) for n in (data.get("nodes") or [])]
    edges = [ConceptEdgeOut(**e) for e in (data.get("edges") or [])]
    return ConceptMapOut(nodes=nodes, edges=edges, generated=True)


@router.post("/{paper_id}/regenerate", status_code=202)
async def regenerate_concept_map(
    background_tasks: BackgroundTasks,
    paper_id: str,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Queue a Celery task to regenerate the concept map for an existing paper.
    Returns 202 Accepted immediately; the map is available once the task completes.
    """
    paper = await get_owned_paper_or_404(db, paper_id, guest_id)
    if paper.status.value != "ready":
        raise HTTPException(status_code=409, detail="Paper is not ready yet")

    if settings.inline_ingestion:
        from app.ingestion.tasks import run_concept_regeneration_job

        background_tasks.add_task(run_concept_regeneration_job, paper_id)
    else:
        from app.ingestion.tasks import regenerate_concept_map as regen_task

        regen_task.apply_async(args=[paper_id], queue="default")
    return {"status": "queued", "paper_id": paper_id}
