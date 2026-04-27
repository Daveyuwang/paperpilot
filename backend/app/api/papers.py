import os
import json
import uuid
import asyncio
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from app.api.guest import require_guest_id, require_guest_id_for_download, get_owned_paper_or_404
from app.db.postgres import get_db, AsyncSessionLocal
from app.db.redis_client import delete_bm25_index
from app.ingestion.celery_app import celery_app
from app.models.orm import Paper, PaperStatus
from app.models.schemas import PaperOut, PaperListItem, GuideQuestionOut, ChunkOut
from app.config import get_settings
from app.rate_limit import limiter

logger = structlog.get_logger()
settings = get_settings()
router = APIRouter()


@router.post("/upload", response_model=PaperOut)
@limiter.limit("5/hour")
async def upload_paper(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    workspace_id: Optional[str] = Form(default=None),
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
        workspace_id=workspace_id,
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
    workspace_id: Optional[str] = Query(default=None),
    guest_id: str = Depends(require_guest_id),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Paper).where(Paper.guest_id == guest_id)
    if workspace_id:
        stmt = stmt.where(Paper.workspace_id == workspace_id)
    result = await db.execute(stmt.order_by(Paper.created_at.desc()))
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

    try:
        await delete_bm25_index(paper_id)
    except Exception as exc:
        logger.warning("paper_bm25_cleanup_failed", paper_id=paper_id, error=str(exc))

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


@router.get("/{paper_id}/ingestion-progress")
async def ingestion_progress(paper_id: str, guest_id: str = Depends(require_guest_id)):
    """SSE endpoint that streams ingestion stage and progress until the paper is ready or errored."""
    async def event_stream():
        while True:
            async with AsyncSessionLocal() as db:
                paper = await db.get(Paper, paper_id)
                if not paper or paper.guest_id != guest_id:
                    yield f"data: {json.dumps({'error': 'not_found'})}\n\n"
                    return
                yield f"data: {json.dumps({'stage': paper.ingestion_stage, 'progress': paper.ingestion_progress, 'status': paper.status.value})}\n\n"
                if paper.status in (PaperStatus.ready, PaperStatus.error):
                    return
            await asyncio.sleep(2)
    return StreamingResponse(event_stream(), media_type="text/event-stream")
