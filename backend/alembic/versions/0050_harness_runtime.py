"""Durable production runs, dependency tasks, attempts and ordered user events."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0050_harness_runtime"
down_revision = "0049_library_covers"
branch_labels = None
depends_on = None


def common():
    return [
        sa.Column("id", pg.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def json_columns(*names):
    return [sa.Column(name, pg.JSONB(), nullable=False) for name in names]


def upgrade():
    op.create_table(
        "production_run",
        *common(),
        sa.Column(
            "project_id", pg.UUID(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_by", pg.UUID(), nullable=False),
        sa.Column("request_key", pg.UUID(), nullable=False),
        sa.Column("work_id", pg.UUID()),
        sa.Column("conversation_id", pg.UUID()),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("engine", sa.String(64), nullable=False),
        sa.Column("model_provider", sa.String(120)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        *json_columns("policy", "context", "plan", "result", "error"),
    )
    op.create_index("ix_production_run_project_id", "production_run", ["project_id"])
    op.create_index("ix_production_run_status", "production_run", ["status"])
    op.create_index(
        "uq_production_request",
        "production_run",
        ["project_id", "created_by", "request_key"],
        unique=True,
    )
    op.create_table(
        "production_task",
        *common(),
        sa.Column(
            "run_id",
            pg.UUID(),
            sa.ForeignKey("production_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parent_id", pg.UUID()),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", pg.UUID()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        *json_columns("dependencies", "specification", "state", "result", "error"),
    )
    op.create_index("ix_production_task_run_id", "production_task", ["run_id"])
    op.create_index("ix_production_task_status", "production_task", ["status"])
    op.create_index(
        "uq_production_task", "production_task", ["run_id", "revision", "key"], unique=True
    )
    op.create_table(
        "production_attempt",
        *common(),
        sa.Column(
            "task_id",
            pg.UUID(),
            sa.ForeignKey("production_task.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("lease_token", pg.UUID(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        *json_columns("operation", "result", "error"),
    )
    op.create_index("ix_production_attempt_task_id", "production_attempt", ["task_id"])
    op.create_index(
        "uq_production_attempt", "production_attempt", ["task_id", "number"], unique=True
    )
    op.create_table(
        "production_event",
        *common(),
        sa.Column(
            "run_id",
            pg.UUID(),
            sa.ForeignKey("production_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("request_key", pg.UUID()),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("actor_id", pg.UUID()),
        *json_columns("payload"),
    )
    op.create_index("ix_production_event_run_id", "production_event", ["run_id"])
    op.create_index(
        "uq_production_event_order", "production_event", ["run_id", "sequence"], unique=True
    )
    op.create_index(
        "uq_production_event_request", "production_event", ["run_id", "request_key"], unique=True
    )


def downgrade():
    for name in ("production_event", "production_attempt", "production_task", "production_run"):
        op.drop_table(name)
