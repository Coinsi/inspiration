"""Checkpointed transcription drafts and explicit annotation provenance."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0014_transcription"
down_revision = "0013_identity_evidence"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "media_annotation", sa.Column("source", JSONB, nullable=False, server_default="{}")
    )
    op.create_table(
        "transcription_run",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version_id", UUID, sa.ForeignKey("media_version.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model_key", sa.String(255), nullable=False),
        sa.Column("language", sa.String(8)),
        sa.Column("total", sa.Integer, nullable=False),
        sa.Column("completed", sa.Integer, nullable=False),
        sa.Column("raw_cues", JSONB, nullable=False),
        sa.Column("cues", JSONB, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("published_revision", sa.Integer),
        sa.Column("cancel_requested", sa.Boolean, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("created_by", UUID, nullable=False),
        *[
            sa.Column(n, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for n in ("created_at", "updated_at")
        ],
    )
    op.create_index("ix_transcription_run_project_id", "transcription_run", ["project_id"])
    op.create_index("ix_transcription_run_version_id", "transcription_run", ["version_id"])


def downgrade():
    op.drop_table("transcription_run")
    op.drop_column("media_annotation", "source")
