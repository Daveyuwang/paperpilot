"""
Celery ingestion task: full pipeline from PDF path to indexed chunks.
"""
from __future__ import annotations
import uuid
import structlog

from app.ingestion.celery_app import celery_app
from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

CONFIDENCE_THRESHOLD = 0.5  # below this, try Nougat fallback


def run_ingestion_job(paper_id: str, pdf_path: str) -> dict:
    """Run the full PDF ingestion pipeline synchronously."""
    from sqlalchemy import create_engine, delete
    from sqlalchemy.orm import Session as SyncSession

    from app.ingestion.pdf_parser import parse_pdf, parse_pdf_nougat
    from app.ingestion.chunker import split_into_chunks
    from app.retrieval.embedder import embed_chunks
    from app.retrieval.bm25_index import build_bm25_index, serialize_bm25
    from app.retrieval.qdrant_client import upsert_chunks
    from app.ingestion.scaffold_pass import generate_question_trail
    from app.models.orm import (
        Paper, Chunk, PaperStatus,
        GuideQuestion, PaperConceptMap,
    )

    engine = create_engine(settings.database_url_sync)

    def _paper_exists() -> bool:
        with SyncSession(engine) as db:
            return db.get(Paper, paper_id) is not None

    def _abort_if_deleted(stage: str) -> bool:
        if _paper_exists():
            return False
        logger.info("ingestion_aborted_deleted", paper_id=paper_id, stage=stage)
        return True

    guest_id_for_llm: str = ""

    def _update_status(status: PaperStatus, error: str | None = None):
        with SyncSession(engine) as db:
            paper = db.get(Paper, paper_id)
            if paper:
                paper.status = status
                if error:
                    paper.error_message = error
                db.commit()

    def _reset_derived_artifacts():
        # Make restart recovery idempotent: a resumed ingestion should replace
        # chunks / guide questions / concept map from any interrupted prior run.
        with SyncSession(engine) as db:
            db.execute(delete(Chunk).where(Chunk.paper_id == paper_id))
            db.execute(delete(GuideQuestion).where(GuideQuestion.paper_id == paper_id))
            db.execute(delete(PaperConceptMap).where(PaperConceptMap.paper_id == paper_id))
            paper = db.get(Paper, paper_id)
            if paper:
                paper.error_message = None
            db.commit()
        try:
            from app.retrieval.qdrant_client import delete_paper_chunks

            delete_paper_chunks(paper_id)
        except Exception as exc:
            logger.warning("qdrant_cleanup_failed", paper_id=paper_id, error=str(exc))

    try:
        _update_status(PaperStatus.processing)
        _reset_derived_artifacts()
        logger.info("ingestion_start", paper_id=paper_id)

        # ── 1. Parse PDF ──────────────────────────────────────────────────
        parsed = parse_pdf(pdf_path)
        if _abort_if_deleted("after_parse"):
            return {"paper_id": paper_id, "status": "deleted"}
        used_nougat = False

        if parsed["confidence"] < CONFIDENCE_THRESHOLD:
            logger.info("low_confidence_using_nougat", paper_id=paper_id, confidence=parsed["confidence"])
            parsed = parse_pdf_nougat(pdf_path)
            if _abort_if_deleted("after_nougat"):
                return {"paper_id": paper_id, "status": "deleted"}
            used_nougat = True

        # ── 2. Chunk ──────────────────────────────────────────────────────
        raw_chunks = parsed["raw_chunks"]
        chunks = split_into_chunks(raw_chunks)
        logger.info("chunks_created", paper_id=paper_id, count=len(chunks))
        if _abort_if_deleted("after_chunking"):
            return {"paper_id": paper_id, "status": "deleted"}

        # ── 3. Persist chunks to Postgres ─────────────────────────────────
        with SyncSession(engine) as db:
            paper = db.get(Paper, paper_id)
            if not paper:
                logger.info("ingestion_aborted_deleted", paper_id=paper_id, stage="before_persist")
                return {"paper_id": paper_id, "status": "deleted"}
            guest_id_for_llm = paper.guest_id or ""
            paper.title = parsed.get("title")
            paper.abstract = parsed.get("abstract")
            paper.section_headers = parsed.get("section_headers")
            paper.page_count = parsed.get("page_count")
            paper.parse_confidence = parsed.get("confidence")
            paper.used_nougat_fallback = used_nougat

            chunk_objs = []
            for chunk in chunks:
                c = Chunk(
                    id=str(uuid.uuid4()),
                    paper_id=paper_id,
                    content=chunk["content"],
                    section_title=chunk.get("section_title"),
                    page_number=chunk.get("page_number"),
                    chunk_index=chunk["chunk_index"],
                    content_type=chunk.get("content_type", "text"),
                    bbox=chunk.get("bbox"),
                )
                db.add(c)
                chunk_objs.append(c)
            db.commit()
            # Refresh to get IDs
            for c in chunk_objs:
                db.refresh(c)
            # Add IDs back to chunks dict for downstream use
            for c, chunk in zip(chunk_objs, chunks):
                chunk["id"] = c.id
                chunk["qdrant_id"] = c.id
        if _abort_if_deleted("after_persist"):
            return {"paper_id": paper_id, "status": "deleted"}

        # ── 4. Embed → Qdrant ─────────────────────────────────────────────
        text_chunks = [c for c in chunks if c.get("content_type", "text") == "text"]
        embeddings = embed_chunks([c["content"] for c in text_chunks])
        if _abort_if_deleted("after_embedding"):
            return {"paper_id": paper_id, "status": "deleted"}
        upsert_chunks(paper_id, text_chunks, embeddings)
        logger.info("qdrant_upsert_done", paper_id=paper_id, count=len(text_chunks))

        # Update Qdrant IDs in Postgres
        with SyncSession(engine) as db:
            for chunk in text_chunks:
                c = db.get(Chunk, chunk["id"])
                if c:
                    c.qdrant_id = chunk["qdrant_id"]
            db.commit()
        if _abort_if_deleted("after_qdrant"):
            return {"paper_id": paper_id, "status": "deleted"}

        # ── 5. BM25 index → Redis ─────────────────────────────────────────
        bm25 = build_bm25_index([c["content"] for c in chunks])
        serialized = serialize_bm25(bm25, [c["id"] for c in chunks])
        import redis as sync_redis
        r = sync_redis.from_url(settings.redis_url, decode_responses=False)
        r.setex(f"bm25:{paper_id}", 60 * 60 * 72, serialized)
        logger.info("bm25_index_stored", paper_id=paper_id)
        if _abort_if_deleted("after_bm25"):
            return {"paper_id": paper_id, "status": "deleted"}

        # ── 6. Scaffold pass: guided questions ────────────────────────────
        language_pref = "en"
        llm_protocol = settings.llm_protocol
        llm_base_url = settings.llm_base_url
        llm_api_key = settings.llm_api_key or (settings.anthropic_api_key if settings.llm_protocol == "anthropic" else "")
        llm_model = settings.llm_model or settings.claude_model
        if guest_id_for_llm:
            try:
                import redis as sync_redis

                r2 = sync_redis.from_url(settings.redis_url, decode_responses=True)
                raw_settings = r2.get(f"guest:{guest_id_for_llm}:llm_settings")
                if raw_settings:
                    import json as _json

                    s = _json.loads(raw_settings)
                    language_pref = (s.get("language") or "en").strip()
                    llm_protocol = s.get("protocol") or llm_protocol
                    llm_base_url = s.get("base_url") or llm_base_url
                    llm_api_key = s.get("api_key") or llm_api_key
                    llm_model = s.get("model") or llm_model
            except Exception:
                language_pref = "en"

        if language_pref == "zh-Hant":
            language_pref = "zh-TW"

        questions = generate_question_trail(
            title=parsed.get("title", ""),
            abstract=parsed.get("abstract", ""),
            section_headers=parsed.get("section_headers", []),
            guest_id=guest_id_for_llm,
            language=language_pref,
            protocol=llm_protocol,
            base_url=llm_base_url,
            api_key=llm_api_key,
            model=llm_model,
        )
        if _abort_if_deleted("after_scaffold_generate"):
            return {"paper_id": paper_id, "status": "deleted"}
        with SyncSession(engine) as db:
            for i, q in enumerate(questions):
                db.add(GuideQuestion(
                    id=str(uuid.uuid4()),
                    paper_id=paper_id,
                    question=q["question"],
                    stage=q["stage"],
                    order_index=i,
                    anchor_sections=q.get("anchor_sections"),
                ))
            db.commit()
        logger.info("guide_questions_stored", paper_id=paper_id, count=len(questions))
        if _abort_if_deleted("after_scaffold_store"):
            return {"paper_id": paper_id, "status": "deleted"}

        _update_status(PaperStatus.ready)
        logger.info("ingestion_ready", paper_id=paper_id)

        # Kick off concept map generation in background (does not block readiness).
        try:
            regenerate_concept_map.apply_async(args=[paper_id], queue="default")
            logger.info("concept_map_queued", paper_id=paper_id)
        except Exception as exc:
            logger.warning("concept_map_queue_failed", paper_id=paper_id, error=str(exc))

        logger.info("ingestion_complete", paper_id=paper_id)
        return {"paper_id": paper_id, "status": "ready"}

    except Exception as exc:
        logger.exception("ingestion_failed", paper_id=paper_id, error=str(exc))
        _update_status(PaperStatus.error, error=str(exc))
        raise


@celery_app.task(bind=True, name="app.ingestion.tasks.ingest_paper", max_retries=2)
def ingest_paper(self, paper_id: str, pdf_path: str) -> dict:
    """
    Full ingestion pipeline:
    1. Parse PDF (PyMuPDF, with Nougat fallback)
    2. Chunk text
    3. Embed chunks → Qdrant
    4. Build BM25 index → Redis
    5. Generate guided question trail
    6. Update paper status in Postgres (ready)
    7. Queue concept map generation (non-blocking)
    """
    try:
        return run_ingestion_job(paper_id, pdf_path)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30)


def run_concept_regeneration_job(paper_id: str) -> dict:
    """Regenerate the concept map for an existing paper synchronously."""
    from datetime import datetime, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SyncSession
    from sqlalchemy import select

    from app.ingestion.concept_extractor import extract_concept_map
    from app.models.orm import Paper, Chunk, PaperConceptMap

    engine = create_engine(settings.database_url_sync)

    # Load all data inside the session — accessing ORM attributes after session
    # closes causes DetachedInstanceError (SQLAlchemy expires objects on close).
    with SyncSession(engine) as db:
        paper = db.get(Paper, paper_id)
        if not paper:
            logger.warning("regenerate_concept_map_paper_not_found", paper_id=paper_id)
            return {"error": "paper not found"}

        # Extract plain strings before the session closes
        paper_title = paper.title or ""
        paper_abstract = paper.abstract or ""
        guest_id_for_llm = paper.guest_id or ""

        chunks_result = db.execute(
            select(Chunk)
            .where(Chunk.paper_id == paper_id)
            .order_by(Chunk.chunk_index)
        )
        # Build plain dicts inside the session so no ORM objects escape
        chunks = [
            {
                "content": c.content,
                "section_title": c.section_title,
                "page_number": c.page_number,
                "content_type": c.content_type,
            }
            for c in chunks_result.scalars().all()
        ]

    concept_map_data = extract_concept_map(
        paper_title=paper_title,
        paper_abstract=paper_abstract,
        chunks=chunks,
        guest_id=guest_id_for_llm,
    )

    with SyncSession(engine) as db:
        record = PaperConceptMap(
            paper_id=paper_id,
            data=concept_map_data,
            generated_at=datetime.now(timezone.utc),
        )
        db.merge(record)
        db.commit()

    logger.info(
        "concept_map_regenerated",
        paper_id=paper_id,
        nodes=len(concept_map_data.get("nodes", [])),
        edges=len(concept_map_data.get("edges", [])),
    )
    return {"paper_id": paper_id, "nodes": len(concept_map_data.get("nodes", []))}


@celery_app.task(name="app.ingestion.tasks.regenerate_concept_map")
def regenerate_concept_map(paper_id: str) -> dict:
    """
    Standalone task to regenerate the concept map for an existing paper.
    Fetches chunks from Postgres, runs LLM extraction, upserts result.
    """
    return run_concept_regeneration_job(paper_id)
