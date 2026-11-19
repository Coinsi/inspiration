"""Keep intentional empty space around picture clips."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0063_clip_spacing"
down_revision = "0062_media_renditions"
branch_labels = depends_on = None


def upgrade():
    for name in ("gap_before_ms", "gap_after_ms"):
        op.add_column(
            "timeline_item", sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        )
    op.create_table(
        "timeline_media_preview",
        sa.Column(
            "source_hash",
            sa.String(128),
            sa.ForeignKey("blob.hash", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("payload", JSONB),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("due_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_timeline_preview_due",
        "timeline_media_preview",
        ["due_at"],
        postgresql_where=sa.text("due_at IS NOT NULL"),
    )


def downgrade():
    op.drop_table("timeline_media_preview")
    op.drop_column("timeline_item", "gap_after_ms")
    op.drop_column("timeline_item", "gap_before_ms")
