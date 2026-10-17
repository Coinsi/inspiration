"""Application-owned conversations and immutable per-run context snapshots."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from alembic import op

revision = "0032_conversations"
down_revision = "0031_media_audio_presence"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversation",
        sa.Column("id", pg.UUID(), primary_key=True),
        sa.Column(
            "project_id", pg.UUID(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_by", pg.UUID(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_conversation_project_id", "conversation", ["project_id"])
    op.create_index(
        "ix_conversation_owner", "conversation", ["project_id", "created_by", "updated_at"]
    )
    op.create_table(
        "conversation_message",
        sa.Column("id", pg.UUID(), primary_key=True),
        sa.Column(
            "conversation_id",
            pg.UUID(),
            sa.ForeignKey("conversation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("request_key", pg.UUID(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("execution_kind", sa.String(40)),
        sa.Column("execution_id", pg.UUID()),
        sa.Column("context", pg.JSONB(), nullable=False),
        sa.Column("progress", pg.JSONB(), nullable=False),
        sa.Column("error", sa.String(1000)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "uq_conversation_ordinal",
        "conversation_message",
        ["conversation_id", "ordinal"],
        unique=True,
    )
    op.create_index(
        "uq_conversation_request",
        "conversation_message",
        ["conversation_id", "request_key", "role"],
        unique=True,
    )
    op.create_index("ix_conversation_execution", "conversation_message", ["execution_id"])
    op.add_column(
        "agent_run", sa.Column("input_context", pg.JSONB(), nullable=False, server_default="{}")
    )


def downgrade():
    op.drop_column("agent_run", "input_context")
    op.drop_table("conversation_message")
    op.drop_table("conversation")
