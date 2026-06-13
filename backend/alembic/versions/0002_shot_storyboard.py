"""shot.storyboard —— 分镜规格(景别/机位/运镜/时长/对白/转场…)JSONB 列

Revision ID: 0002_shot_storyboard
Revises: 0001_initial
Create Date: 2026-06-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002_shot_storyboard"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "shot",
        sa.Column("storyboard", JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("shot", "storyboard")
