"""Persist bounded Agent tool runs and review states."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0010_agent"
down_revision = "0009_canvas"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "agent_run",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_by", UUID, nullable=False),
        sa.Column("request_key", UUID, nullable=False),
        sa.Column("goal", sa.String(8000), nullable=False),
        sa.Column("engine", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("scope", JSONB, nullable=False),
        sa.Column("max_turns", sa.Integer, nullable=False),
        sa.Column("turns", sa.Integer, nullable=False),
        sa.Column("steps", JSONB, nullable=False),
        sa.Column("result", sa.String(8000)),
        sa.Column("error", sa.String(1000)),
        sa.Column("claim_id", UUID),
        *(
            sa.Column(n, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for n in ("created_at", "updated_at")
        ),
    )
    op.create_index("ix_agent_run_project_id", "agent_run", ["project_id"])
    op.create_index(
        "uq_agent_request", "agent_run", ["project_id", "created_by", "request_key"], unique=True
    )


def downgrade():
    op.drop_table("agent_run")
