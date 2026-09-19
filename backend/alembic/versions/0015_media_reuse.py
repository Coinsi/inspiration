"""Project-owned fixed media reuse and retained provenance."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0015_media_reuse"
down_revision = "0014_transcription"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "media_reuse",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("source_version_id", UUID, sa.ForeignKey("media_version.id"), nullable=False),
        sa.Column("target_version_id", UUID, sa.ForeignKey("media_version.id"), nullable=False),
        sa.Column(
            "target_project_id",
            UUID,
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", UUID, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("source_ordinal", sa.Integer, nullable=False),
        *[
            sa.Column(n, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for n in ("created_at", "updated_at")
        ],
        sa.UniqueConstraint("target_version_id"),
        sa.UniqueConstraint(
            "source_version_id", "target_project_id", name="uq_media_reuse_project"
        ),
    )
    op.create_index("ix_media_reuse_source_version_id", "media_reuse", ["source_version_id"])
    op.create_index("ix_media_reuse_target_project_id", "media_reuse", ["target_project_id"])


def downgrade():
    op.drop_table("media_reuse")
