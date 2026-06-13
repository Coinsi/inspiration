"""script.content_blocks —— 剧本正文(typed blocks)JSONB 列 + source_chapter_id 溯源

Revision ID: 0003_script_content_blocks
Revises: 0002_shot_storyboard
Create Date: 2026-06-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0003_script_content_blocks"
down_revision: str | None = "0002_shot_storyboard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "script",
        sa.Column("content_blocks", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "script",
        sa.Column(
            "source_chapter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("chapter.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("script", "source_chapter_id")
    op.drop_column("script", "content_blocks")
