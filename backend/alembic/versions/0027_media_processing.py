"""Persistent WISE and MediaCMS work attached to immutable library versions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0027_media_processing"
down_revision = "0026_personal_model_services"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "media_processing_run",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("version_id", sa.UUID(), sa.ForeignKey("media_version.id"), nullable=False),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("user.id")),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("capabilities", JSONB(), nullable=False),
        sa.Column("source_hash", sa.String(64), sa.ForeignKey("blob.hash"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("remote_id", sa.String(255)),
        sa.Column("progress", JSONB(), nullable=False),
        sa.Column("outputs", JSONB(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("next_poll_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("provider IN ('wise', 'mediacms')", name="ck_media_processing_provider"),
        sa.CheckConstraint(
            "operation IN ('process', 'delete')", name="ck_media_processing_operation"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'waiting', 'ready', 'failed', 'canceled')",
            name="ck_media_processing_status",
        ),
    )
    for column in ("version_id", "status", "next_poll_at"):
        op.create_index(f"ix_media_processing_run_{column}", "media_processing_run", [column])
    op.create_index(
        "uq_media_processing_active",
        "media_processing_run",
        ["version_id", "provider"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'processing', 'waiting')"),
    )


def downgrade():
    op.drop_table("media_processing_run")
