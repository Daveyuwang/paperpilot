"""add durable deep research run events

Revision ID: 20260731_deep_events
Revises: 20260731_deep_artifacts
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260731_deep_events"
down_revision: str | None = "20260731_deep_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EVENT_SCHEMA_VERSION = "deep-research-event.v1"
EVENT_TYPES = (
    "run_started",
    "phase_started",
    "phase_completed",
    "subquestion_upserted",
    "subquestion_progressed",
    "evaluation_started",
    "evaluation_completed",
    "route_selected",
    "artifact_version_created",
    "checkpoint_saved",
    "budget_updated",
    "synthesis_section_updated",
    "run_finished",
    "protocol_error",
)

POSTGRES_GUARD_FUNCTION = "guard_deep_research_run_event_mutation"
POSTGRES_GUARD_TRIGGER = "trg_deep_research_run_events_guard"
SQLITE_OWNER_TRIGGER = "trg_deep_research_run_events_owner"
SQLITE_UPDATE_TRIGGER = "trg_deep_research_run_events_no_update"
SQLITE_DELETE_TRIGGER = "trg_deep_research_run_events_no_delete"


def _create_storage_guards(bind: sa.Connection) -> None:
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                f"""
                CREATE OR REPLACE FUNCTION {POSTGRES_GUARD_FUNCTION}()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF TG_OP = 'INSERT' THEN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM workflow_runs AS run
                            JOIN workspaces AS workspace
                              ON workspace.id = run.workspace_id
                            WHERE run.id = NEW.run_id
                              AND run.workspace_id = NEW.workspace_id
                              AND run.guest_id = NEW.guest_id
                              AND run.run_type::text = 'deep_research'
                              AND workspace.guest_id = NEW.guest_id
                        ) THEN
                            RAISE EXCEPTION
                                'Deep Research event ownership mismatch'
                                USING ERRCODE = '23503';
                        END IF;
                        RETURN NEW;
                    END IF;
                    IF TG_OP = 'UPDATE' THEN
                        RAISE EXCEPTION
                            'Deep Research run events are immutable'
                            USING ERRCODE = '55000';
                    END IF;
                    IF TG_OP = 'DELETE' AND pg_trigger_depth() <= 1 THEN
                        RAISE EXCEPTION
                            'Deep Research run events are append-only'
                            USING ERRCODE = '55000';
                    END IF;
                    RETURN OLD;
                END;
                $$
                """
            )
        )
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER {POSTGRES_GUARD_TRIGGER}
                BEFORE INSERT OR UPDATE OR DELETE
                ON deep_research_run_events
                FOR EACH ROW
                EXECUTE FUNCTION {POSTGRES_GUARD_FUNCTION}()
                """
            )
        )
        return
    if bind.dialect.name == "sqlite":
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER IF NOT EXISTS {SQLITE_OWNER_TRIGGER}
                BEFORE INSERT ON deep_research_run_events
                FOR EACH ROW
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM workflow_runs AS run
                    JOIN workspaces AS workspace
                      ON workspace.id = run.workspace_id
                    WHERE run.id = NEW.run_id
                      AND run.workspace_id = NEW.workspace_id
                      AND run.guest_id = NEW.guest_id
                      AND run.run_type = 'deep_research'
                      AND workspace.guest_id = NEW.guest_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'Deep Research event ownership mismatch');
                END
                """
            )
        )
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER IF NOT EXISTS {SQLITE_UPDATE_TRIGGER}
                BEFORE UPDATE ON deep_research_run_events
                FOR EACH ROW
                BEGIN
                    SELECT RAISE(ABORT, 'Deep Research run events are immutable');
                END
                """
            )
        )
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER IF NOT EXISTS {SQLITE_DELETE_TRIGGER}
                BEFORE DELETE ON deep_research_run_events
                FOR EACH ROW
                WHEN EXISTS (
                    SELECT 1 FROM workflow_runs WHERE id = OLD.run_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'Deep Research run events are append-only');
                END
                """
            )
        )


def upgrade() -> None:
    op.create_table(
        "deep_research_run_events",
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
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=128), nullable=False),
        sa.Column(
            "emitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("cycle", sa.Integer(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("corpus_version", sa.Integer(), nullable=False),
        sa.Column("report_version", sa.Integer(), nullable=True),
        sa.Column("checkpoint_id", sa.String(length=255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "seq",
            name="uq_deep_research_run_events_run_seq",
        ),
        sa.UniqueConstraint(
            "run_id",
            "event_id",
            name="uq_deep_research_run_events_run_event_id",
        ),
        sa.CheckConstraint(
            "seq > 0",
            name="ck_deep_research_run_events_seq_positive",
        ),
        sa.CheckConstraint(
            f"schema_version = '{EVENT_SCHEMA_VERSION}'",
            name="ck_deep_research_run_events_schema_version",
        ),
        sa.CheckConstraint(
            "type IN ("
            + ", ".join(f"'{event_type}'" for event_type in EVENT_TYPES)
            + ")",
            name="ck_deep_research_run_events_type_supported",
        ),
        sa.CheckConstraint(
            "cycle >= 0",
            name="ck_deep_research_run_events_cycle_nonnegative",
        ),
        sa.CheckConstraint(
            "plan_version >= 0",
            name="ck_deep_research_run_events_plan_version_nonnegative",
        ),
        sa.CheckConstraint(
            "corpus_version >= 0",
            name="ck_deep_research_run_events_corpus_version_nonnegative",
        ),
        sa.CheckConstraint(
            "report_version IS NULL OR report_version >= 1",
            name="ck_deep_research_run_events_report_version_positive",
        ),
    )
    op.create_index(
        "ix_deep_research_run_events_run_id",
        "deep_research_run_events",
        ["run_id"],
    )
    op.create_index(
        "ix_deep_research_run_events_workspace_id",
        "deep_research_run_events",
        ["workspace_id"],
    )
    op.create_index(
        "ix_deep_research_run_events_guest_id",
        "deep_research_run_events",
        ["guest_id"],
    )
    op.create_index(
        "ix_deep_research_run_events_run_seq",
        "deep_research_run_events",
        ["run_id", "seq"],
    )
    op.create_index(
        "ix_deep_research_run_events_owner_seq",
        "deep_research_run_events",
        ["run_id", "workspace_id", "guest_id", "seq"],
    )
    _create_storage_guards(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_table("deep_research_run_events")
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                f"DROP FUNCTION IF EXISTS {POSTGRES_GUARD_FUNCTION}()"
            )
        )
