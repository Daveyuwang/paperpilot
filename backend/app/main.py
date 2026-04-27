import asyncio
import os
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import get_settings
from app.rate_limit import limiter
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.db.postgres import AsyncSessionLocal, init_db
from app.api import papers, sessions, concepts, ws, settings as settings_api, sources, drafts, deep_research, proposal_plan, workspaces, preferences, workflow_runs
from app.ingestion.tasks import run_ingestion_job
from app.models.orm import Paper, PaperStatus
from app.tracing import flush_tracing

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
    flush_tracing()
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

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(papers.router, prefix="/api/papers", tags=["papers"])
app.include_router(workspaces.router, prefix="/api/workspaces", tags=["workspaces"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(concepts.router, prefix="/api/concepts", tags=["concepts"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"])
app.include_router(sources.router, prefix="/api/sources", tags=["sources"])
app.include_router(drafts.router, prefix="/api/drafts", tags=["drafts"])
app.include_router(deep_research.router, prefix="/api/deep-research", tags=["deep-research"])
app.include_router(proposal_plan.router, prefix="/api/proposal-plan", tags=["proposal-plan"])
app.include_router(workflow_runs.router, prefix="/api/workflow-runs", tags=["workflow-runs"])
app.include_router(preferences.router, prefix="/api/preferences", tags=["preferences"])
app.include_router(ws.router, prefix="/ws", tags=["websocket"])


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"status": "ok", "service": "paperpilot-api"}
