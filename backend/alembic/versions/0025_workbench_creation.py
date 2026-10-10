"""Workbench generation targets and per-run text model choice."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0025_workbench_creation"
down_revision = "0024_creative_models"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "workbench_creation",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("request_key", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("request", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "uq_creation_request",
        "workbench_creation",
        ["project_id", "created_by", "request_key"],
        unique=True,
    )
    op.create_index("ix_workbench_creation_project_id", "workbench_creation", ["project_id"])
    op.add_column("agent_run", sa.Column("model_provider", sa.String(120), nullable=True))


def downgrade():
    op.drop_column("agent_run", "model_provider")
    op.drop_table("workbench_creation")
