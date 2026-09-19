"""Timeline captions and durable, optimistic editing revisions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0008_timeline_tracks"
down_revision = "0007_media_index"
branch_labels = depends_on = None


def upgrade():
    op.add_column("timeline", sa.Column("revision", sa.Integer, nullable=False, server_default="0"))
    op.add_column("timeline", sa.Column("subtitles", JSONB, nullable=False, server_default="[]"))
    op.create_table(
        "timeline_revision",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("timeline_id", UUID, sa.ForeignKey("timeline.id"), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("created_by", UUID),
        *(
            sa.Column(n, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for n in ("created_at", "updated_at")
        ),
    )
    op.create_index(
        "uq_timeline_revision", "timeline_revision", ["timeline_id", "revision"], unique=True
    )


def downgrade():
    op.drop_table("timeline_revision")
    op.drop_column("timeline", "subtitles")
    op.drop_column("timeline", "revision")
