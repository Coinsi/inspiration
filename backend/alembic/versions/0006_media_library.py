"""Video library, resumable version uploads and pinned shot usages."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0006_media_library"
down_revision = "0005_setting"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade():
    op.create_table(
        "library_media",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_library_media_project_id", "library_media", ["project_id"])
    op.create_table(
        "media_version",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "media_id", UUID(as_uuid=True), sa.ForeignKey("library_media.id"), nullable=False
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("uploaded_bytes", sa.BigInteger(), nullable=False),
        sa.Column("chunk_hashes", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("original_hash", sa.String(128), sa.ForeignKey("blob.hash")),
        sa.Column("proxy_hash", sa.String(128), sa.ForeignKey("blob.hash")),
        sa.Column("poster_hash", sa.String(128), sa.ForeignKey("blob.hash")),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.UniqueConstraint("media_id", "ordinal", name="uq_media_version_ordinal"),
        sa.CheckConstraint(
            "size_bytes > 0 AND uploaded_bytes >= 0 AND uploaded_bytes <= size_bytes",
            name="ck_media_upload_size",
        ),
        *timestamps(),
    )
    op.create_index("ix_media_version_media_id", "media_version", ["media_id"])
    op.create_index("ix_media_version_status", "media_version", ["status"])
    op.create_table(
        "media_usage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id", UUID(as_uuid=True), sa.ForeignKey("media_version.id"), nullable=False
        ),
        sa.Column("shot_id", UUID(as_uuid=True), sa.ForeignKey("shot.id"), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_media_usage_range"),
        sa.UniqueConstraint(
            "version_id", "shot_id", "start_ms", "end_ms", "purpose", name="uq_media_usage"
        ),
        *timestamps(),
    )
    op.create_index("ix_media_usage_version_id", "media_usage", ["version_id"])
    op.create_index("ix_media_usage_shot_id", "media_usage", ["shot_id"])


def downgrade():
    op.drop_table("media_usage")
    op.drop_table("media_version")
    op.drop_table("library_media")
