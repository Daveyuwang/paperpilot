"""add research director persistence

Revision ID: 20260730_research_director
Revises:
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260730_research_director"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


research_artifact_status = postgresql.ENUM(
    "draft",
    "reviewed",
    "approved",
    "superseded",
    "handed_off",
    name="research_artifact_status",
    create_type=False,
)


def _ownership_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "workspace_id",
            sa.String(length=36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("guest_id", sa.String(length=64), nullable=False),
    ]


def _lifecycle_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "status",
            research_artifact_status,
            nullable=False,
            server_default=sa.text("'draft'::research_artifact_status"),
        ),
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
    ]


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


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_workspaces_prerequisite(bind)
    research_artifact_status.create(bind, checkfirst=True)

    if not _has_table(bind, "research_projects"):
        op.create_table(
            "research_projects",
            sa.Column("id", sa.String(length=36), nullable=False),
            *_ownership_columns(),
            sa.Column("title", sa.String(length=512), nullable=False),
            sa.Column("objective", sa.Text(), nullable=True),
            *_lifecycle_columns(),
            sa.Column(
                "content",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'::json"),
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        bind,
        "ix_research_projects_workspace_id",
        "research_projects",
        ["workspace_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_research_projects_guest_id",
        "research_projects",
        ["guest_id"],
    )

    if not _has_table(bind, "research_plan_versions"):
        op.create_table(
            "research_plan_versions",
            sa.Column("id", sa.String(length=36), nullable=False),
            *_ownership_columns(),
            sa.Column(
                "research_project_id",
                sa.String(length=36),
                sa.ForeignKey("research_projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version_number", sa.Integer(), nullable=False),
            *_lifecycle_columns(),
            sa.Column(
                "content",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'::json"),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "research_project_id",
                "version_number",
                name="uq_research_plan_versions_project_version",
            ),
        )
    _create_index_if_missing(
        bind,
        "ix_research_plan_versions_workspace_id",
        "research_plan_versions",
        ["workspace_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_research_plan_versions_guest_id",
        "research_plan_versions",
        ["guest_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_research_plan_versions_research_project_id",
        "research_plan_versions",
        ["research_project_id"],
    )

    if not _has_table(bind, "research_plan_reviews"):
        op.create_table(
            "research_plan_reviews",
            sa.Column("id", sa.String(length=36), nullable=False),
            *_ownership_columns(),
            sa.Column(
                "research_project_id",
                sa.String(length=36),
                sa.ForeignKey("research_projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "research_plan_version_id",
                sa.String(length=36),
                sa.ForeignKey("research_plan_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "review_round",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            ),
            *_lifecycle_columns(),
            sa.Column(
                "review",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'::json"),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "research_plan_version_id",
                "review_round",
                name="uq_research_plan_reviews_version_round",
            ),
        )
    _create_index_if_missing(
        bind,
        "ix_research_plan_reviews_workspace_id",
        "research_plan_reviews",
        ["workspace_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_research_plan_reviews_guest_id",
        "research_plan_reviews",
        ["guest_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_research_plan_reviews_research_project_id",
        "research_plan_reviews",
        ["research_project_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_research_plan_reviews_research_plan_version_id",
        "research_plan_reviews",
        ["research_plan_version_id"],
    )

    if not _has_table(bind, "research_handoff_bundles"):
        op.create_table(
            "research_handoff_bundles",
            sa.Column("id", sa.String(length=36), nullable=False),
            *_ownership_columns(),
            sa.Column(
                "research_project_id",
                sa.String(length=36),
                sa.ForeignKey("research_projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "research_plan_version_id",
                sa.String(length=36),
                sa.ForeignKey("research_plan_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version_number", sa.Integer(), nullable=False),
            *_lifecycle_columns(),
            sa.Column(
                "content",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'::json"),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "research_project_id",
                "version_number",
                name="uq_research_handoff_bundles_project_version",
            ),
        )
    _create_index_if_missing(
        bind,
        "ix_research_handoff_bundles_workspace_id",
        "research_handoff_bundles",
        ["workspace_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_research_handoff_bundles_guest_id",
        "research_handoff_bundles",
        ["guest_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_research_handoff_bundles_research_project_id",
        "research_handoff_bundles",
        ["research_project_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_research_handoff_bundles_research_plan_version_id",
        "research_handoff_bundles",
        ["research_plan_version_id"],
    )

    if not _has_table(bind, "research_idempotency_receipts"):
        op.create_table(
            "research_idempotency_receipts",
            sa.Column("id", sa.String(length=36), nullable=False),
            *_ownership_columns(),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("operation", sa.String(length=64), nullable=False),
            sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="in_progress",
            ),
            sa.Column("owner_token", sa.String(length=36), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("response_status_code", sa.Integer(), nullable=True),
            sa.Column("response_payload", sa.JSON(), nullable=True),
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
            sa.UniqueConstraint(
                "workspace_id",
                "guest_id",
                "idempotency_key",
                name="uq_research_idempotency_receipts_scope_key",
            ),
        )
    _create_index_if_missing(
        bind,
        "ix_research_idempotency_receipts_workspace_id",
        "research_idempotency_receipts",
        ["workspace_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_research_idempotency_receipts_guest_id",
        "research_idempotency_receipts",
        ["guest_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in (
        "research_idempotency_receipts",
        "research_handoff_bundles",
        "research_plan_reviews",
        "research_plan_versions",
        "research_projects",
    ):
        if _has_table(bind, table_name):
            op.drop_table(table_name)
    research_artifact_status.drop(bind, checkfirst=True)
