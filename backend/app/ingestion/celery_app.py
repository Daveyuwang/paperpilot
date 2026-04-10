from celery import Celery
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "paperpilot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.ingestion.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.ingestion.tasks.ingest_paper": {"queue": "ingestion"},
    },
)
