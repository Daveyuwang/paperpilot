"""add immutable deep research artifact versions

Revision ID: 20260731_deep_artifacts
Revises: 20260730_research_director
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260731_deep_artifacts"
down_revision: str | None = "20260730_research_director"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ARTIFACT_KINDS = (
    "plan",
    "sub_report",
    "pre_synthesis_evaluation",
    "controller_transition",
    "report_candidate",
    "post_synthesis_evaluation",
    "terminal_decision",
)
WORKFLOW_RUN_TYPES = (
    "deep_research",
    "proposal",
    "plan",
    "deliverable_draft",
)
WORKFLOW_RUN_STATUSES = (
    "running",
    "completed",
    "failed",
    "interrupted",
    "incomplete",
)

workflow_run_type = postgresql.ENUM(
    *WORKFLOW_RUN_TYPES,
    name="workflowruntype",
    create_type=False,
)
workflow_run_status = postgresql.ENUM(
    *WORKFLOW_RUN_STATUSES,
    name="workflowrunstatus",
    create_type=False,
)


def _has_table(bind: sa.Connection, table_name: str) -> bool:
    return sa.inspect(bind).has_table(table_name)


def _create_index_if_missing(
    bind: sa.Connection,
    index_name: str,
    table_name: str,
    columns: list[str],
) -> None:
    existing = {
        item["name"]
        for item in sa.inspect(bind).get_indexes(table_name)
        if item.get("name")
    }
    if index_name not in existing:
        op.create_index(index_name, table_name, columns)


def _ensure_workspaces_prerequisite(bind: sa.Connection) -> None:
    if not _has_table(bind, "workspaces"):
        op.create_table(
            "workspaces",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("guest_id", sa.String(length=64), nullable=False),
            sa.Column(
                "title",
                sa.String(length=512),
                nullable=False,
                server_default="My Research Workspace",
            ),
            sa.Column("objective", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        bind,
        "ix_workspaces_guest_id",
        "workspaces",
        ["guest_id"],
    )


def _postgres_enum_identity(
    bind: sa.Connection,
    *,
    table_name: str,
    column_name: str,
    fallback_type_name: str,
) -> tuple[str, str] | None:
    row = bind.execute(
        sa.text(
            """
            SELECT enum_namespace.nspname, enum_type.typname
            FROM pg_class AS table_class
            JOIN pg_namespace AS table_namespace
              ON table_namespace.oid = table_class.relnamespace
            JOIN pg_attribute AS attribute
              ON attribute.attrelid = table_class.oid
            JOIN pg_type AS enum_type
              ON enum_type.oid = attribute.atttypid
            JOIN pg_namespace AS enum_namespace
              ON enum_namespace.oid = enum_type.typnamespace
            WHERE table_namespace.nspname = current_schema()
              AND table_class.relname = :table_name
              AND attribute.attname = :column_name
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
              AND enum_type.typtype = 'e'
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).first()
    if row is not None:
        return str(row[0]), str(row[1])
    fallback = bind.execute(
        sa.text(
            """
            SELECT namespace.nspname, enum_type.typname
            FROM pg_type AS enum_type
            JOIN pg_namespace AS namespace
              ON namespace.oid = enum_type.typnamespace
            WHERE namespace.nspname = current_schema()
              AND enum_type.typname = :type_name
              AND enum_type.typtype = 'e'
            """
        ),
        {"type_name": fallback_type_name},
    ).first()
    if fallback is None:
        return None
    return str(fallback[0]), str(fallback[1])


def _postgres_enum_has_label(
    bind: sa.Connection,
    *,
    schema_name: str,
    type_name: str,
    label: str,
) -> bool:
    return bool(
        bind.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_type AS enum_type
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = enum_type.typnamespace
                    JOIN pg_enum AS enum_value
                      ON enum_value.enumtypid = enum_type.oid
                    WHERE namespace.nspname = :schema_name
                      AND enum_type.typname = :type_name
                      AND enum_value.enumlabel = :label
                )
                """
            ),
            {
                "schema_name": schema_name,
                "type_name": type_name,
                "label": label,
            },
        )
    )


def _ensure_postgres_workflow_enums(bind: sa.Connection) -> None:
    workflow_run_type.create(bind, checkfirst=True)
    status_identity = _postgres_enum_identity(
        bind,
        table_name="workflow_runs",
        column_name="status",
        fallback_type_name="workflowrunstatus",
    )
    if status_identity is None:
        workflow_run_status.create(bind, checkfirst=True)
        return
    schema_name, type_name = status_identity
    if _postgres_enum_has_label(
        bind,
        schema_name=schema_name,
        type_name=type_name,
        label="incomplete",
    ):
        return

    # PostgreSQL cannot safely consume a newly-added enum value until the
    # transaction that added it commits. Alembic's autocommit block provides a
    # migration-safe boundary for both old and current PostgreSQL releases.
    preparer = bind.dialect.identifier_preparer
    qualified_name = (
        f"{preparer.quote(schema_name)}.{preparer.quote(type_name)}"
    )
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                f"ALTER TYPE {qualified_name} "
                "ADD VALUE IF NOT EXISTS 'incomplete'"
            )
        )


def _ensure_workflow_runs_prerequisite(bind: sa.Connection) -> None:
    if bind.dialect.name == "postgresql":
        _ensure_postgres_workflow_enums(bind)
        run_type: sa.types.TypeEngine = workflow_run_type
        run_status: sa.types.TypeEngine = workflow_run_status
    else:
        run_type = sa.Enum(
            *WORKFLOW_RUN_TYPES,
            name="workflowruntype",
            native_enum=False,
            create_constraint=True,
        )
        run_status = sa.Enum(
            *WORKFLOW_RUN_STATUSES,
            name="workflowrunstatus",
            native_enum=False,
            create_constraint=True,
        )

    if not _has_table(bind, "workflow_runs"):
        op.create_table(
            "workflow_runs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column(
                "workspace_id",
                sa.String(length=36),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("guest_id", sa.String(length=64), nullable=False),
            sa.Column("run_type", run_type, nullable=False),
            sa.Column(
                "status",
                run_status,
                nullable=False,
                server_default="running",
            ),
            sa.Column("input_payload", sa.JSON(), nullable=True),
            sa.Column("current_stage", sa.String(length=64), nullable=True),
            sa.Column("stages_completed", sa.JSON(), nullable=True),
            sa.Column("artifacts", sa.JSON(), nullable=True),
            sa.Column("error", sa.JSON(), nullable=True),
            sa.Column("token_usage", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        bind,
        "ix_workflow_runs_workspace_id",
        "workflow_runs",
        ["workspace_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_workflow_runs_guest_id",
        "workflow_runs",
        ["guest_id"],
    )


def _create_artifact_table(bind: sa.Connection) -> None:
    artifact_kind = sa.Enum(
        *ARTIFACT_KINDS,
        name="ck_deep_research_artifact_versions_kind",
        native_enum=False,
        create_constraint=True,
        length=32,
    )
    if not _has_table(bind, "deep_research_artifact_versions"):
        op.create_table(
            "deep_research_artifact_versions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column(
                "run_id",
                sa.String(length=36),
                sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "workspace_id",
                sa.String(length=36),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("guest_id", sa.String(length=64), nullable=False),
            sa.Column("artifact_kind", artifact_kind, nullable=False),
            sa.Column(
                "logical_artifact_id",
                sa.String(length=255),
                nullable=False,
            ),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column(
                "plan_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "controller_cycle",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "schema_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            ),
            sa.Column(
                "parent_version_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "deep_research_artifact_versions.id",
                    ondelete="NO ACTION",
                    deferrable=True,
                    initially="DEFERRED",
                ),
                nullable=True,
            ),
            sa.Column(
                "source_checkpoint_id",
                sa.String(length=255),
                nullable=True,
            ),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("write_key", sa.String(length=255), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "run_id",
                "write_key",
                name="uq_deep_research_artifact_versions_run_write_key",
            ),
            sa.UniqueConstraint(
                "run_id",
                "artifact_kind",
                "logical_artifact_id",
                "version_number",
                name="uq_deep_research_artifact_versions_logical_version",
            ),
            sa.CheckConstraint(
                "version_number > 0",
                name="ck_deep_research_artifact_versions_version_positive",
            ),
            sa.CheckConstraint(
                "plan_version >= 0",
                name=(
                    "ck_deep_research_artifact_versions_plan_version_nonnegative"
                ),
            ),
            sa.CheckConstraint(
                "controller_cycle >= 0",
                name="ck_deep_research_artifact_versions_cycle_nonnegative",
            ),
            sa.CheckConstraint(
                "schema_version > 0",
                name=(
                    "ck_deep_research_artifact_versions_schema_version_positive"
                ),
            ),
            sa.CheckConstraint(
                "length(trim(logical_artifact_id)) > 0",
                name="ck_deep_research_artifact_versions_logical_id_nonempty",
            ),
            sa.CheckConstraint(
                "length(trim(write_key)) > 0",
                name="ck_deep_research_artifact_versions_write_key_nonempty",
            ),
            sa.CheckConstraint(
                "length(content_hash) = 64",
                name="ck_deep_research_artifact_versions_hash_length",
            ),
        )

    indexes = {
        "ix_deep_research_artifact_versions_run_id": ["run_id"],
        "ix_deep_research_artifact_versions_workspace_id": ["workspace_id"],
        "ix_deep_research_artifact_versions_guest_id": ["guest_id"],
        "ix_deep_research_artifact_versions_parent_version_id": [
            "parent_version_id"
        ],
        "ix_deep_research_artifact_versions_source_checkpoint_id": [
            "source_checkpoint_id"
        ],
        "ix_deep_research_artifact_versions_run_kind_cycle": [
            "run_id",
            "artifact_kind",
            "controller_cycle",
        ],
        "ix_deep_research_artifact_versions_owner_cycle": [
            "run_id",
            "workspace_id",
            "guest_id",
            "controller_cycle",
        ],
    }
    for index_name, columns in indexes.items():
        _create_index_if_missing(
            bind,
            index_name,
            "deep_research_artifact_versions",
            columns,
        )


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_workspaces_prerequisite(bind)
    _ensure_workflow_runs_prerequisite(bind)
    _create_artifact_table(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "deep_research_artifact_versions"):
        op.drop_table("deep_research_artifact_versions")
    if _has_table(bind, "workflow_runs"):
        op.execute(
            sa.text(
                "UPDATE workflow_runs SET status = 'failed' "
                "WHERE status = 'incomplete'"
            )
        )
    # PostgreSQL enum labels cannot be removed without rewriting dependent
    # columns. Retaining the now-unused ``incomplete`` label makes rollback
    # non-destructive; rows are mapped to the older terminal ``failed`` state
    # above so the previous application enum can still load them.
