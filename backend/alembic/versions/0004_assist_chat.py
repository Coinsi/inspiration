"""assist_chat —— 贯穿式 AI 对话助手会话表

Revision ID: 0004_assist_chat
Revises: 0003_script_content_blocks
Create Date: 2026-06-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0004_assist_chat"
down_revision: str | None = "0003_script_content_blocks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assist_chat",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("messages", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_assist_chat_target", "assist_chat", ["target_type", "target_id"])
    op.create_index("ix_assist_chat_project_id", "assist_chat", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_assist_chat_project_id", table_name="assist_chat")
    op.drop_index("ix_assist_chat_target", table_name="assist_chat")
    op.drop_table("assist_chat")
