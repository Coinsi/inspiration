"""Unified model identities and durable upstream submission attempts."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0024_creative_models"
down_revision = "0023_blog"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "creative_model",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("modality", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("definition", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_creative_model_project", "creative_model", ["project_id"])
    op.add_column(
        "generation_job", sa.Column("routing_state", JSONB(), nullable=False, server_default="{}")
    )


def downgrade():
    op.drop_column("generation_job", "routing_state")
    op.drop_table("creative_model")
