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
from sqlalchemy.pool import NullPool


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260731_deep_research_artifacts.py"
)
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ARTIFACT_TABLE = "deep_research_artifact_versions"
EXPECTED_ARTIFACT_COLUMNS = {
    "id",
    "run_id",
    "workspace_id",
    "guest_id",
    "artifact_kind",
    "logical_artifact_id",
    "version_number",
    "plan_version",
    "controller_cycle",
    "schema_version",
    "parent_version_id",
    "source_checkpoint_id",
    "content_hash",
    "write_key",
    "payload",
    "created_at",
}
EXPECTED_INDEXES = {
    "ix_deep_research_artifact_versions_run_id",
    "ix_deep_research_artifact_versions_workspace_id",
    "ix_deep_research_artifact_versions_guest_id",
    "ix_deep_research_artifact_versions_parent_version_id",
    "ix_deep_research_artifact_versions_source_checkpoint_id",
    "ix_deep_research_artifact_versions_run_kind_cycle",
    "ix_deep_research_artifact_versions_owner_cycle",
}
EXPECTED_UNIQUES = {
    "uq_deep_research_artifact_versions_run_write_key",
    "uq_deep_research_artifact_versions_logical_version",
}
EXPECTED_CHECKS = {
    "ck_deep_research_artifact_versions_kind",
    "ck_deep_research_artifact_versions_version_positive",
    "ck_deep_research_artifact_versions_plan_version_nonnegative",
    "ck_deep_research_artifact_versions_cycle_nonnegative",
    "ck_deep_research_artifact_versions_schema_version_positive",
    "ck_deep_research_artifact_versions_logical_id_nonempty",
    "ck_deep_research_artifact_versions_write_key_nonempty",
    "ck_deep_research_artifact_versions_hash_length",
}


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "deep_research_artifact_migration_under_test",
        MIGRATION_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load migration: {MIGRATION_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_migration(connection: sa.Connection, operation: str) -> None:
    migration = _load_migration()
    context = MigrationContext.configure(connection)
    with context.begin_transaction():
        with Operations.context(context):
            getattr(migration, operation)()


class DeepResearchArtifactSQLiteMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = sa.create_engine("sqlite://")
        self.connection = self.engine.connect()

    def tearDown(self) -> None:
        self.connection.close()
        self.engine.dispose()

    def _assert_artifact_schema(self) -> None:
        inspector = sa.inspect(self.connection)
        self.assertTrue(
            {"workspaces", "workflow_runs", ARTIFACT_TABLE}.issubset(
                inspector.get_table_names()
            )
        )
        self.assertEqual(
            {item["name"] for item in inspector.get_columns(ARTIFACT_TABLE)},
            EXPECTED_ARTIFACT_COLUMNS,
        )
        self.assertTrue(
            EXPECTED_INDEXES.issubset(
                {
                    item["name"]
                    for item in inspector.get_indexes(ARTIFACT_TABLE)
                    if item.get("name")
                }
            )
        )
        self.assertEqual(
            {
                item["name"]
                for item in inspector.get_unique_constraints(ARTIFACT_TABLE)
            },
            EXPECTED_UNIQUES,
        )
        self.assertTrue(
            EXPECTED_CHECKS.issubset(
                {
                    item["name"]
                    for item in inspector.get_check_constraints(ARTIFACT_TABLE)
                    if item.get("name")
                }
            )
        )
        foreign_keys = inspector.get_foreign_keys(ARTIFACT_TABLE)
        run_fk = next(
            item for item in foreign_keys if item["referred_table"] == "workflow_runs"
        )
        workspace_fk = next(
            item for item in foreign_keys if item["referred_table"] == "workspaces"
        )
        parent_fk = next(
            item for item in foreign_keys if item["referred_table"] == ARTIFACT_TABLE
        )
        self.assertEqual(run_fk.get("options", {}).get("ondelete"), "CASCADE")
        self.assertEqual(
            workspace_fk.get("options", {}).get("ondelete"), "CASCADE"
        )
        # SQLite reports its default NO ACTION policy as an omitted option.
        self.assertIn(
            parent_fk.get("options", {}).get("ondelete"),
            {None, "NO ACTION"},
        )

    def test_blank_upgrade_is_idempotent_and_downgrade_is_non_destructive(self):
        _run_migration(self.connection, "upgrade")
        self._assert_artifact_schema()

        # A create_all-first or partially migrated deployment may already own
        # every table/index. The revision must converge without duplicate DDL.
        _run_migration(self.connection, "upgrade")
        self._assert_artifact_schema()

        workspace_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        self.connection.execute(
            sa.text(
                "INSERT INTO workspaces (id, guest_id, title) "
                "VALUES (:id, 'owner', 'Migration test')"
            ),
            {"id": workspace_id},
        )
        self.connection.execute(
            sa.text(
                "INSERT INTO workflow_runs "
                "(id, workspace_id, guest_id, run_type, status) "
                "VALUES (:id, :workspace_id, 'owner', "
                "'deep_research', 'incomplete')"
            ),
            {"id": run_id, "workspace_id": workspace_id},
        )
        self.connection.commit()

        _run_migration(self.connection, "downgrade")
        inspector = sa.inspect(self.connection)
        self.assertNotIn(ARTIFACT_TABLE, inspector.get_table_names())
        self.assertIn("workflow_runs", inspector.get_table_names())
        self.assertEqual(
            self.connection.scalar(
                sa.text("SELECT status FROM workflow_runs WHERE id = :id"),
                {"id": run_id},
            ),
            "failed",
        )


def _sync_postgres_url(raw_url: str):
    url = make_url(raw_url)
    if not url.drivername.startswith("postgres"):
        raise unittest.SkipTest("TEST_DATABASE_URL must identify PostgreSQL")
    return url.set(drivername="postgresql+psycopg2")


@unittest.skipUnless(
    TEST_DATABASE_URL,
    "Set TEST_DATABASE_URL to run PostgreSQL enum compatibility tests.",
)
class DeepResearchArtifactPostgresMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema_name = f"deep_artifact_migration_{uuid.uuid4().hex}"
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

    def _status_labels(self) -> list[str]:
        return list(
            self.connection.scalars(
                sa.text(
                    """
                    SELECT enum_value.enumlabel
                    FROM pg_type AS enum_type
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = enum_type.typnamespace
                    JOIN pg_enum AS enum_value
                      ON enum_value.enumtypid = enum_type.oid
                    WHERE namespace.nspname = current_schema()
                      AND enum_type.typname = 'workflowrunstatus'
                    ORDER BY enum_value.enumsortorder
                    """
                )
            )
        )

    def test_old_workflow_status_enum_converges_additively(self):
        self.connection.execute(
            sa.text(
                "CREATE TYPE workflowrunstatus AS ENUM "
                "('running', 'completed', 'failed', 'interrupted')"
            )
        )
        self.connection.commit()

        _run_migration(self.connection, "upgrade")

        self.assertEqual(
            self._status_labels(),
            ["running", "completed", "failed", "interrupted", "incomplete"],
        )
        self.assertIn(ARTIFACT_TABLE, sa.inspect(self.connection).get_table_names())
