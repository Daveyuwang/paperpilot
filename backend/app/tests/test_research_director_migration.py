from __future__ import annotations

import importlib.util
import os
import unittest
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

import app.models.orm  # noqa: F401  # register every ORM table with Base.metadata
from app.db.postgres import Base

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260730_research_director.py"
)
RESEARCH_TABLES = {
    "research_projects",
    "research_plan_versions",
    "research_plan_reviews",
    "research_handoff_bundles",
    "research_idempotency_receipts",
}
REQUIRED_INDEXES = {
    "research_projects": {
        "ix_research_projects_workspace_id",
        "ix_research_projects_guest_id",
    },
    "research_plan_versions": {
        "ix_research_plan_versions_workspace_id",
        "ix_research_plan_versions_guest_id",
        "ix_research_plan_versions_research_project_id",
    },
    "research_plan_reviews": {
        "ix_research_plan_reviews_workspace_id",
        "ix_research_plan_reviews_guest_id",
        "ix_research_plan_reviews_research_project_id",
        "ix_research_plan_reviews_research_plan_version_id",
    },
    "research_handoff_bundles": {
        "ix_research_handoff_bundles_workspace_id",
        "ix_research_handoff_bundles_guest_id",
        "ix_research_handoff_bundles_research_project_id",
        "ix_research_handoff_bundles_research_plan_version_id",
    },
    "research_idempotency_receipts": {
        "ix_research_idempotency_receipts_workspace_id",
        "ix_research_idempotency_receipts_guest_id",
    },
}
REQUIRED_COLUMNS = {
    "research_projects": {
        "id",
        "workspace_id",
        "guest_id",
        "title",
        "objective",
        "status",
        "content",
        "created_at",
        "updated_at",
    },
    "research_plan_versions": {
        "id",
        "workspace_id",
        "guest_id",
        "research_project_id",
        "version_number",
        "status",
        "content",
        "created_at",
        "updated_at",
    },
    "research_plan_reviews": {
        "id",
        "workspace_id",
        "guest_id",
        "research_project_id",
        "research_plan_version_id",
        "review_round",
        "status",
        "review",
        "created_at",
        "updated_at",
    },
    "research_handoff_bundles": {
        "id",
        "workspace_id",
        "guest_id",
        "research_project_id",
        "research_plan_version_id",
        "version_number",
        "status",
        "content",
        "created_at",
        "updated_at",
    },
    "research_idempotency_receipts": {
        "id",
        "workspace_id",
        "guest_id",
        "idempotency_key",
        "operation",
        "request_fingerprint",
        "status",
        "owner_token",
        "lease_expires_at",
        "response_status_code",
        "response_payload",
        "created_at",
        "updated_at",
        "completed_at",
    },
}


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "research_director_migration_under_test",
        MIGRATION_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load migration: {MIGRATION_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sync_postgres_url(raw_url: str):
    url = make_url(raw_url)
    if not url.drivername.startswith("postgres"):
        raise unittest.SkipTest("TEST_DATABASE_URL must identify PostgreSQL")
    return url.set(drivername="postgresql+psycopg2")


@unittest.skipUnless(
    TEST_DATABASE_URL,
    "Set TEST_DATABASE_URL to run PostgreSQL migration compatibility tests.",
)
class ResearchDirectorMigrationTests(unittest.TestCase):
    def setUp(self):
        self.migration = _load_migration()
        self.schema_name = f"research_director_migration_{uuid.uuid4().hex}"
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

    def tearDown(self):
        self.connection.execute(sa.text("SET search_path TO public"))
        self.connection.commit()
        self.connection.execute(
            sa.schema.DropSchema(
                self.schema_name,
                cascade=True,
                if_exists=True,
            )
        )
        self.connection.commit()
        self.connection.close()
        self.engine.dispose()

    def _run(self, operation: str) -> None:
        context = MigrationContext.configure(self.connection)
        with Operations.context(context):
            getattr(self.migration, operation)()
        self.connection.commit()

    def _table_names(self) -> set[str]:
        return set(sa.inspect(self.connection).get_table_names())

    def _enum_exists(self) -> bool:
        return bool(
            self.connection.scalar(
                sa.text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_type AS type
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = type.typnamespace
                        WHERE type.typname = 'research_artifact_status'
                          AND namespace.nspname = current_schema()
                    )
                    """
                )
            )
        )

    def _assert_feature_schema_present(self) -> None:
        tables = self._table_names()
        self.assertIn("workspaces", tables)
        self.assertTrue(RESEARCH_TABLES.issubset(tables))
        inspector = sa.inspect(self.connection)
        workspace_columns = {
            item["name"] for item in inspector.get_columns("workspaces")
        }
        self.assertEqual(
            workspace_columns,
            {"id", "guest_id", "title", "objective", "created_at", "updated_at"},
        )
        workspace_indexes = {
            item["name"]
            for item in inspector.get_indexes("workspaces")
        }
        self.assertIn("ix_workspaces_guest_id", workspace_indexes)
        for table_name, required_indexes in REQUIRED_INDEXES.items():
            actual_columns = {
                item["name"] for item in inspector.get_columns(table_name)
            }
            self.assertEqual(actual_columns, REQUIRED_COLUMNS[table_name])
            actual_indexes = {
                item["name"]
                for item in inspector.get_indexes(table_name)
            }
            self.assertTrue(required_indexes.issubset(actual_indexes))
        receipt_uniques = {
            item["name"]
            for item in inspector.get_unique_constraints(
                "research_idempotency_receipts"
            )
        }
        self.assertIn(
            "uq_research_idempotency_receipts_scope_key",
            receipt_uniques,
        )
        receipt_workspace_fks = [
            item
            for item in inspector.get_foreign_keys(
                "research_idempotency_receipts"
            )
            if item["referred_table"] == "workspaces"
        ]
        self.assertEqual(len(receipt_workspace_fks), 1)
        self.assertEqual(
            receipt_workspace_fks[0].get("options", {}).get("ondelete"),
            "CASCADE",
        )
        self.assertTrue(self._enum_exists())

    def _assert_feature_schema_removed(self) -> None:
        tables = self._table_names()
        self.assertIn("workspaces", tables)
        self.assertTrue(RESEARCH_TABLES.isdisjoint(tables))
        self.assertFalse(self._enum_exists())

    def test_blank_database_upgrade_then_downgrade(self):
        self._run("upgrade")
        self._assert_feature_schema_present()

        self._run("downgrade")
        self._assert_feature_schema_removed()

    def test_create_all_first_upgrade_then_downgrade(self):
        Base.metadata.create_all(self.connection)
        self.connection.commit()
        self.assertTrue(RESEARCH_TABLES.issubset(self._table_names()))

        self._run("upgrade")
        self._assert_feature_schema_present()

        self._run("downgrade")
        self._assert_feature_schema_removed()
        self.assertIn("papers", self._table_names())
