"""Run Alembic under a PostgreSQL advisory lock.

Every production replica may invoke this module before starting workers. The
database-scoped lock serializes those invocations, so only one migration runner
can advance the schema at a time and a crashed runner releases the lock with its
connection.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

import app.models.orm  # noqa: F401  # register every legacy table in metadata
from app.config import get_settings
from app.db.postgres import Base

MIGRATION_ADVISORY_LOCK_ID = 0x504150455250494C  # "PAPERPIL"
BACKEND_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_DIRECTOR_TABLES = {
    "research_projects",
    "research_plan_versions",
    "research_plan_reviews",
    "research_handoff_bundles",
    "research_idempotency_receipts",
}


def _ensure_legacy_schema(connection: Connection) -> None:
    """Compatibility bootstrap for tables that predate Alembic in this repo.

    Research Director tables remain migration-owned. Existing PaperPilot tables
    historically came from ``create_all``; creating only those missing legacy
    tables under the same process-wide database lock keeps a blank deployment
    functional without letting API or Celery workers race DDL. A future legacy
    baseline revision can remove this compatibility step.
    """

    legacy_tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name not in RESEARCH_DIRECTOR_TABLES
    ]
    Base.metadata.create_all(
        bind=connection,
        tables=legacy_tables,
        checkfirst=True,
    )


def upgrade_head() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
            )
            # PostgreSQL advisory locks are session-scoped, so committing this
            # implicit transaction does not release the serialization gate.
            connection.commit()
            try:
                command.upgrade(config, "head")
                _ensure_legacy_schema(connection)
                connection.commit()
            finally:
                if connection.in_transaction():
                    connection.rollback()
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
                )
                connection.commit()
    finally:
        engine.dispose()


if __name__ == "__main__":
    upgrade_head()
