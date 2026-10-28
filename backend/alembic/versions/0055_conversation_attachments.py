"""Private conversation uploads with durable message provenance and immutable CAS references."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0055_conversation_attachments"
down_revision = "0054_sdk_video_handles"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversation_attachment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("metadata_revision", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("uploaded_bytes", sa.BigInteger(), nullable=False),
        sa.Column("chunk_hashes", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.String(500)),
        sa.Column("blob_hash", sa.String(128), sa.ForeignKey("blob.hash")),
        sa.Column("thumbnail_hash", sa.String(128), sa.ForeignKey("blob.hash")),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("has_audio", sa.Boolean(), nullable=False),
        sa.Column("favorite", sa.Boolean(), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True)),
        sa.Column("hidden_at", sa.DateTime(timezone=True)),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversation.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversation_message.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_conversation_attachment_project_id", "conversation_attachment", ["project_id"]
    )
    op.create_index(
        "uq_attachment_request",
        "conversation_attachment",
        ["project_id", "created_by", "request_key"],
        unique=True,
    )
    op.create_index(
        "ix_attachment_owner_saved",
        "conversation_attachment",
        ["project_id", "created_by", "saved_at"],
    )


def downgrade():
    op.drop_table("conversation_attachment")
