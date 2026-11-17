"""Durable creative tools with idempotent requests and candidate receipts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0059_creative_tool_tasks"
down_revision = "0058_script_generation_tasks"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "creative_tool_task",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column(
            "project_id", UUID(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("target_id", UUID(), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("request_key", UUID(), nullable=False),
        sa.Column("created_by", UUID(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("result", JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_creative_tool_task_project_id", "creative_tool_task", ["project_id"])
    op.create_index(
        "uq_creative_tool_request", "creative_tool_task", ["project_id", "request_key"], unique=True
    )
    op.create_index(
        "ix_creative_tool_target",
        "creative_tool_task",
        ["project_id", "operation", "target_id", "created_at"],
    )


def downgrade():
    op.drop_table("creative_tool_task")
