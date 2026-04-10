import os
import uuid
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.guest import require_guest_id, require_guest_id_for_download, get_owned_paper_or_404
from app.db.postgres import get_db
from app.db.redis_client import delete_bm25_index
from app.ingestion.celery_app import celery_app
from app.models.orm import Paper, PaperStatus
from app.models.schemas import PaperOut, PaperListItem, GuideQuestionOut, ChunkOut
from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()
router = APIRouter()


@router.post("/upload", response_model=PaperOut)
async def upload_paper(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    """Upload a PDF and enqueue the ingestion pipeline."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.max_upload_size_mb:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.max_upload_size_mb} MB limit.",
        )

    paper_id = str(uuid.uuid4())
    save_path = os.path.join(settings.upload_dir, f"{paper_id}.pdf")
    os.makedirs(settings.upload_dir, exist_ok=True)

    with open(save_path, "wb") as f:
        f.write(content)

    paper = Paper(
        id=paper_id,
        guest_id=guest_id,
        filename=file.filename,
        status=PaperStatus.pending,
    )
    db.add(paper)
    await db.commit()
    await db.refresh(paper)

    paper.status = PaperStatus.processing
    if settings.inline_ingestion:
        from app.ingestion.tasks import run_ingestion_job

        paper.celery_task_id = None
        background_tasks.add_task(run_ingestion_job, paper_id, save_path)
    else:
        from app.ingestion.tasks import ingest_paper

        task = ingest_paper.delay(paper_id, save_path)
        paper.celery_task_id = task.id
    await db.commit()
    await db.refresh(paper)

    logger.info("paper_uploaded", paper_id=paper_id, filename=file.filename, guest_id=guest_id)
    return PaperOut.model_validate(paper)


@router.get("/", response_model=list[PaperListItem])
async def list_papers(
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Paper)
        .where(Paper.guest_id == guest_id)
        .order_by(Paper.created_at.desc())
    )
    return [PaperListItem.model_validate(p) for p in result.scalars()]


@router.get("/{paper_id}", response_model=PaperOut)
async def get_paper(
    paper_id: str,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    paper = await get_owned_paper_or_404(db, paper_id, guest_id)
    return PaperOut.model_validate(paper)


@router.delete("/{paper_id}", status_code=204)
async def delete_paper(
    paper_id: str,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    paper = await get_owned_paper_or_404(db, paper_id, guest_id)
    if paper.celery_task_id:
        try:
            celery_app.control.revoke(paper.celery_task_id, terminate=True)
            logger.info("paper_ingestion_revoked", paper_id=paper_id, task_id=paper.celery_task_id)
        except Exception as exc:
            logger.warning("paper_ingestion_revoke_failed", paper_id=paper_id, error=str(exc))

    try:
        from app.retrieval.qdrant_client import delete_paper_chunks

        delete_paper_chunks(paper_id)
    except Exception as exc:
        logger.warning("paper_qdrant_cleanup_failed", paper_id=paper_id, error=str(exc))

    await delete_bm25_index(paper_id)

    # Remove PDF file
    pdf_path = os.path.join(settings.upload_dir, f"{paper_id}.pdf")
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    await db.delete(paper)
    await db.commit()


@router.get("/{paper_id}/questions", response_model=list[GuideQuestionOut])
async def get_guide_questions(
    paper_id: str,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    from app.models.orm import GuideQuestion

    await get_owned_paper_or_404(db, paper_id, guest_id)
    result = await db.execute(
        select(GuideQuestion)
        .where(GuideQuestion.paper_id == paper_id)
        .order_by(GuideQuestion.order_index)
    )
    return [GuideQuestionOut.model_validate(q) for q in result.scalars()]


@router.get("/{paper_id}/chunks", response_model=list[ChunkOut])
async def get_chunks(
    paper_id: str,
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    from app.models.orm import Chunk

    await get_owned_paper_or_404(db, paper_id, guest_id)
    result = await db.execute(
        select(Chunk)
        .where(Chunk.paper_id == paper_id)
        .order_by(Chunk.chunk_index)
    )
    return [ChunkOut.model_validate(c) for c in result.scalars()]


@router.get("/{paper_id}/pdf")
async def get_pdf(
    paper_id: str,
    guest_id: str = Depends(require_guest_id_for_download),
    db: AsyncSession = Depends(get_db),
):
    """Return the PDF file for in-browser rendering."""
    from fastapi.responses import FileResponse

    await get_owned_paper_or_404(db, paper_id, guest_id)
    pdf_path = os.path.join(settings.upload_dir, f"{paper_id}.pdf")
    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="PDF file not found.")
    return FileResponse(pdf_path, media_type="application/pdf")
