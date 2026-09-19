"""Owned media search indices, sampled frames and human annotations."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0007_media_index"
down_revision = "0006_media_library"
branch_labels = depends_on = None


def timestamps():
    return [
        sa.Column(n, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
        for n in ("created_at", "updated_at")
    ]


def upgrade():
    op.create_table(
        "media_index",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("version_id", UUID, sa.ForeignKey("media_version.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model_key", sa.String(255), nullable=False),
        sa.Column("total", sa.Integer, nullable=False),
        sa.Column("completed", sa.Integer, nullable=False),
        sa.Column("cancel_requested", sa.Boolean, nullable=False),
        sa.Column("error", sa.Text),
        *timestamps(),
    )
    op.create_index("ix_media_index_version_id", "media_index", ["version_id"])
    op.create_index("ix_media_index_status", "media_index", ["status"])
    op.create_table(
        "media_segment",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("index_id", UUID, sa.ForeignKey("media_index.id"), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("start_ms", sa.BigInteger, nullable=False),
        sa.Column("end_ms", sa.BigInteger, nullable=False),
        sa.Column("frames", JSONB, nullable=False),
        sa.Column("embedding", sa.ARRAY(sa.Float), nullable=False),
        sa.UniqueConstraint("index_id", "ordinal", name="uq_media_segment_ordinal"),
        *timestamps(),
    )
    op.create_index("ix_media_segment_index_id", "media_segment", ["index_id"])
    op.create_table(
        "media_annotation",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("version_id", UUID, sa.ForeignKey("media_version.id"), nullable=False),
        sa.Column("start_ms", sa.BigInteger, nullable=False),
        sa.Column("end_ms", sa.BigInteger, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_by", UUID, sa.ForeignKey("user.id"), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_media_annotation_version_id", "media_annotation", ["version_id"])


def downgrade():
    op.drop_table("media_annotation")
    op.drop_table("media_segment")
    op.drop_table("media_index")
