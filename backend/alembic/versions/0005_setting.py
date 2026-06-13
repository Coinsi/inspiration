"""setting + setting_extraction —— 故事圣经(设定库)与全书提取任务

Revision ID: 0005_setting
Revises: 0004_assist_chat
Create Date: 2026-06-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0005_setting"
down_revision: str | None = "0004_assist_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "setting",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_id", UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_chapters", JSONB(), nullable=False, server_default="[]"),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "novel_id", "category", "name", name="uq_setting_key"),
    )
    op.create_index("ix_setting_project_id", "setting", ["project_id"])
    op.create_index("ix_setting_novel_id", "setting", ["novel_id"])
    op.create_index("ix_setting_project_category", "setting", ["project_id", "category"])

    op.create_table(
        "setting_extraction",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("total_chapters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("done_chapters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_setting_extraction_project_id", "setting_extraction", ["project_id"])
    op.create_index("ix_setting_extraction_novel_id", "setting_extraction", ["novel_id"])


def downgrade() -> None:
    op.drop_table("setting_extraction")
    op.drop_table("setting")
