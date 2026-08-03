from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.orm import (
    DeepResearchRunEvent,
    ImmutableDeepResearchRunEventError,
    WorkflowRun,
)


VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"
ARTIFACT_MIGRATION_PATH = (
    VERSIONS_DIR / "20260731_deep_research_artifacts.py"
)
EVENT_MIGRATION_PATH = (
    VERSIONS_DIR / "20260731_deep_research_run_events.py"
)
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
EVENT_TABLE = "deep_research_run_events"
EXPECTED_COLUMNS = {
    "id",
    "run_id",
    "workspace_id",
    "guest_id",
    "seq",
    "event_id",
    "schema_version",
    "type",
    "emitted_at",
    "cycle",
    "plan_version",
    "corpus_version",
    "report_version",
    "checkpoint_id",
    "payload",
}
EXPECTED_INDEXES = {
    "ix_deep_research_run_events_run_id",
    "ix_deep_research_run_events_workspace_id",
    "ix_deep_research_run_events_guest_id",
    "ix_deep_research_run_events_run_seq",
    "ix_deep_research_run_events_owner_seq",
}
EXPECTED_UNIQUES = {
    "uq_deep_research_run_events_run_seq",
    "uq_deep_research_run_events_run_event_id",
}
EXPECTED_CHECKS = {
    "ck_deep_research_run_events_seq_positive",
    "ck_deep_research_run_events_schema_version",
    "ck_deep_research_run_events_type_supported",
    "ck_deep_research_run_events_cycle_nonnegative",
    "ck_deep_research_run_events_plan_version_nonnegative",
    "ck_deep_research_run_events_corpus_version_nonnegative",
    "ck_deep_research_run_events_report_version_positive",
}


def _load_migration(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_migration(
    connection: sa.Connection,
    path: Path,
    operation: str,
) -> None:
    migration = _load_migration(path, f"migration_{path.stem}_{operation}")
    context = MigrationContext.configure(connection)
    with context.begin_transaction():
        with Operations.context(context):
            getattr(migration, operation)()


def _upgrade_chain(connection: sa.Connection) -> None:
    _run_migration(connection, ARTIFACT_MIGRATION_PATH, "upgrade")
    _run_migration(connection, EVENT_MIGRATION_PATH, "upgrade")


def _assert_event_schema(test: unittest.TestCase, connection: sa.Connection) -> None:
    inspector = sa.inspect(connection)
    test.assertIn(EVENT_TABLE, inspector.get_table_names())
    test.assertEqual(
        {item["name"] for item in inspector.get_columns(EVENT_TABLE)},
        EXPECTED_COLUMNS,
    )
    test.assertTrue(
        EXPECTED_INDEXES.issubset(
            {
                item["name"]
                for item in inspector.get_indexes(EVENT_TABLE)
                if item.get("name")
            }
        )
    )
    test.assertEqual(
        {
            item["name"]
            for item in inspector.get_unique_constraints(EVENT_TABLE)
        },
        EXPECTED_UNIQUES,
    )
    test.assertTrue(
        EXPECTED_CHECKS.issubset(
            {
                item["name"]
                for item in inspector.get_check_constraints(EVENT_TABLE)
                if item.get("name")
            }
        )
    )
    foreign_keys = inspector.get_foreign_keys(EVENT_TABLE)
    run_fk = next(
        item for item in foreign_keys if item["referred_table"] == "workflow_runs"
    )
    workspace_fk = next(
        item for item in foreign_keys if item["referred_table"] == "workspaces"
    )
    test.assertEqual(run_fk.get("options", {}).get("ondelete"), "CASCADE")
    test.assertEqual(workspace_fk.get("options", {}).get("ondelete"), "CASCADE")


class DeepResearchRunEventSQLiteMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = sa.create_engine("sqlite://")
        self.connection = self.engine.connect()
        self.connection.execute(sa.text("PRAGMA foreign_keys = ON"))

    def tearDown(self) -> None:
        self.connection.close()
        self.engine.dispose()

    def test_upgrade_creates_portable_event_contract_and_downgrade_is_scoped(
        self,
    ) -> None:
        _upgrade_chain(self.connection)
        _assert_event_schema(self, self.connection)

        workspace_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())
        row_id = str(uuid.uuid4())
        self.connection.execute(
            sa.text(
                "INSERT INTO workspaces (id, guest_id, title) "
                "VALUES (:id, 'event-owner', 'Event migration test')"
            ),
            {"id": workspace_id},
        )
        self.connection.execute(
            sa.text(
                "INSERT INTO workflow_runs "
                "(id, workspace_id, guest_id, run_type, status) "
                "VALUES (:id, :workspace_id, 'event-owner', "
                "'deep_research', 'running')"
            ),
            {"id": run_id, "workspace_id": workspace_id},
        )
        self.connection.execute(
            sa.text(
                "INSERT INTO deep_research_run_events "
                "(id, run_id, workspace_id, guest_id, seq, event_id, "
                "schema_version, type, cycle, plan_version, corpus_version, "
                "payload) VALUES "
                "(:id, :run_id, :workspace_id, 'event-owner', 1, :event_id, "
                "'deep-research-event.v1', 'run_started', 0, 0, 0, '{}')"
            ),
            {
                "id": row_id,
                "run_id": run_id,
                "workspace_id": workspace_id,
                "event_id": event_id,
            },
        )
        self.connection.commit()
        self.assertEqual(
            self.connection.scalar(
                sa.text(
                    "SELECT seq FROM deep_research_run_events "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ),
            1,
        )
        trigger_names = set(
            self.connection.scalars(
                sa.text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger' AND tbl_name = :table_name"
                ),
                {"table_name": EVENT_TABLE},
            )
        )
        self.assertTrue(
            {
                "trg_deep_research_run_events_owner",
                "trg_deep_research_run_events_no_update",
                "trg_deep_research_run_events_no_delete",
            }.issubset(trigger_names)
        )
        with Session(bind=self.connection, expire_on_commit=False) as session:
            stored = session.get(DeepResearchRunEvent, row_id)
            self.assertIsNotNone(stored)
            assert stored is not None
            stored.type = "run_finished"
            with self.assertRaises(ImmutableDeepResearchRunEventError):
                session.flush()
            session.rollback()
        with Session(bind=self.connection, expire_on_commit=False) as session:
            stored = session.get(DeepResearchRunEvent, row_id)
            self.assertIsNotNone(stored)
            assert stored is not None
            session.delete(stored)
            with self.assertRaises(ImmutableDeepResearchRunEventError):
                session.flush()
            session.rollback()
        with self.assertRaises(sa.exc.IntegrityError):
            with self.connection.begin_nested():
                self.connection.execute(
                    sa.text(
                        "UPDATE deep_research_run_events "
                        "SET type = 'run_finished' WHERE id = :id"
                    ),
                    {"id": row_id},
                )
        with self.assertRaises(sa.exc.IntegrityError):
            with self.connection.begin_nested():
                self.connection.execute(
                    sa.text(
                        "DELETE FROM deep_research_run_events WHERE id = :id"
                    ),
                    {"id": row_id},
                )
        with self.assertRaises(sa.exc.IntegrityError):
            with self.connection.begin_nested():
                self.connection.execute(
                    sa.text(
                        "INSERT INTO deep_research_run_events "
                        "(id, run_id, workspace_id, guest_id, seq, event_id, "
                        "schema_version, type, cycle, plan_version, "
                        "corpus_version, payload) VALUES "
                        "(:id, :run_id, :workspace_id, 'wrong-owner', 2, "
                        ":event_id, 'deep-research-event.v1', 'run_started', "
                        "0, 0, 0, '{}')"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "run_id": run_id,
                        "workspace_id": workspace_id,
                        "event_id": str(uuid.uuid4()),
                    },
                )

        # Owner-scoped direct deletion is forbidden, while lifecycle deletion
        # remains possible through the owning run's ON DELETE CASCADE.
        with Session(bind=self.connection, expire_on_commit=False) as session:
            run = session.get(WorkflowRun, run_id)
            self.assertIsNotNone(run)
            assert run is not None
            self.assertEqual(len(run.deep_research_run_events), 1)
            session.delete(run)
            session.commit()
        self.assertEqual(
            self.connection.scalar(
                sa.text(
                    "SELECT count(*) FROM deep_research_run_events "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ),
            0,
        )
        columns = {
            item["name"]: item
            for item in sa.inspect(self.connection).get_columns(EVENT_TABLE)
        }
        self.assertFalse(columns["cycle"]["nullable"])
        self.assertFalse(columns["plan_version"]["nullable"])
        self.assertFalse(columns["corpus_version"]["nullable"])
        self.assertTrue(columns["report_version"]["nullable"])

        _run_migration(self.connection, EVENT_MIGRATION_PATH, "downgrade")
        inspector = sa.inspect(self.connection)
        self.assertNotIn(EVENT_TABLE, inspector.get_table_names())
        self.assertIn("deep_research_artifact_versions", inspector.get_table_names())
        self.assertIn("workflow_runs", inspector.get_table_names())


def _sync_postgres_url(raw_url: str):
    url = make_url(raw_url)
    if not url.drivername.startswith("postgres"):
        raise unittest.SkipTest("TEST_DATABASE_URL must identify PostgreSQL")
    return url.set(drivername="postgresql+psycopg2")


@unittest.skipUnless(
    TEST_DATABASE_URL,
    "Set TEST_DATABASE_URL to run PostgreSQL migration compatibility tests.",
)
class DeepResearchRunEventPostgresMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema_name = f"deep_event_migration_{uuid.uuid4().hex}"
        self.engine = sa.create_engine(
            _sync_postgres_url(TEST_DATABASE_URL or ""),
            poolclass=NullPool,
        )
        self.connection = self.engine.connect()
        self.connection.execute(sa.schema.CreateSchema(self.schema_name))
        self.connection.commit()
        self.connection.execute(
            sa.text(f'SET search_path TO "{self.schema_name}"')
        )
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.execute(sa.text("SET search_path TO public"))
        self.connection.commit()
        self.connection.execute(
            sa.schema.DropSchema(self.schema_name, cascade=True, if_exists=True)
        )
        self.connection.commit()
        self.connection.close()
        self.engine.dispose()

    def test_upgrade_and_downgrade_match_postgres_schema(self) -> None:
        _upgrade_chain(self.connection)
        _assert_event_schema(self, self.connection)

        workspace_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        row_id = str(uuid.uuid4())
        self.connection.execute(
            sa.text(
                "INSERT INTO workspaces (id, guest_id, title) "
                "VALUES (:id, 'event-owner', 'Postgres event test')"
            ),
            {"id": workspace_id},
        )
        self.connection.execute(
            sa.text(
                "INSERT INTO workflow_runs "
                "(id, workspace_id, guest_id, run_type, status) "
                "VALUES (:id, :workspace_id, 'event-owner', "
                "'deep_research', 'running')"
            ),
            {"id": run_id, "workspace_id": workspace_id},
        )
        self.connection.execute(
            sa.text(
                "INSERT INTO deep_research_run_events "
                "(id, run_id, workspace_id, guest_id, seq, event_id, "
                "schema_version, type, cycle, plan_version, corpus_version, "
                "payload) VALUES (:id, :run_id, :workspace_id, "
                "'event-owner', 1, :event_id, 'deep-research-event.v1', "
                "'run_started', 0, 0, 0, '{}')"
            ),
            {
                "id": row_id,
                "run_id": run_id,
                "workspace_id": workspace_id,
                "event_id": str(uuid.uuid4()),
            },
        )
        self.connection.commit()
        self.assertIn(
            "trg_deep_research_run_events_guard",
            set(
                self.connection.scalars(
                    sa.text(
                        "SELECT trigger_name FROM information_schema.triggers "
                        "WHERE event_object_schema = current_schema() "
                        "AND event_object_table = :table_name"
                    ),
                    {"table_name": EVENT_TABLE},
                )
            ),
        )
        with self.assertRaises(sa.exc.DBAPIError):
            with self.connection.begin_nested():
                self.connection.execute(
                    sa.text(
                        "UPDATE deep_research_run_events "
                        "SET type = 'run_finished' WHERE id = :id"
                    ),
                    {"id": row_id},
                )
        with self.assertRaises(sa.exc.DBAPIError):
            with self.connection.begin_nested():
                self.connection.execute(
                    sa.text(
                        "DELETE FROM deep_research_run_events WHERE id = :id"
                    ),
                    {"id": row_id},
                )
        with self.assertRaises(sa.exc.DBAPIError):
            with self.connection.begin_nested():
                self.connection.execute(
                    sa.text(
                        "INSERT INTO deep_research_run_events "
                        "(id, run_id, workspace_id, guest_id, seq, event_id, "
                        "schema_version, type, cycle, plan_version, "
                        "corpus_version, payload) VALUES "
                        "(:id, :run_id, :workspace_id, 'wrong-owner', 2, "
                        ":event_id, 'deep-research-event.v1', 'run_started', "
                        "0, 0, 0, '{}')"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "run_id": run_id,
                        "workspace_id": workspace_id,
                        "event_id": str(uuid.uuid4()),
                    },
                )

        self.connection.execute(
            sa.text("DELETE FROM workflow_runs WHERE id = :id"),
            {"id": run_id},
        )
        self.connection.commit()
        self.assertEqual(
            self.connection.scalar(
                sa.text(
                    "SELECT count(*) FROM deep_research_run_events "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ),
            0,
        )

        _run_migration(self.connection, EVENT_MIGRATION_PATH, "downgrade")
        self.assertNotIn(EVENT_TABLE, sa.inspect(self.connection).get_table_names())
