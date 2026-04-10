import asyncio
import os
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import get_settings
from app.db.postgres import AsyncSessionLocal, init_db
from app.api import papers, sessions, concepts, ws
from app.ingestion.tasks import run_ingestion_job
from app.models.orm import Paper, PaperStatus

logger = structlog.get_logger()
settings = get_settings()


async def _resume_processing_papers() -> None:
    if not settings.inline_ingestion:
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Paper).where(Paper.status == PaperStatus.processing))
        papers_to_resume = list(result.scalars())

    for paper in papers_to_resume:
        pdf_path = os.path.join(settings.upload_dir, f"{paper.id}.pdf")
        if not os.path.exists(pdf_path):
            async with AsyncSessionLocal() as db:
                record = await db.get(Paper, paper.id)
                if record and record.status == PaperStatus.processing:
                    record.status = PaperStatus.error
                    record.error_message = "Upload file missing after service restart."
                    await db.commit()
            logger.warning("ingestion_resume_missing_pdf", paper_id=paper.id)
            continue

        logger.info("ingestion_resume_scheduled", paper_id=paper.id)
        asyncio.create_task(asyncio.to_thread(run_ingestion_job, paper.id, pdf_path))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", env=settings.environment)
    await init_db()
    await _resume_processing_papers()
    yield
    logger.info("shutdown")


app = FastAPI(
    title="PaperPilot API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(papers.router, prefix="/api/papers", tags=["papers"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(concepts.router, prefix="/api/concepts", tags=["concepts"])
app.include_router(ws.router, prefix="/ws", tags=["websocket"])


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"status": "ok", "service": "paperpilot-api"}
