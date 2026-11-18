"""Administrator maintained provider onboarding templates."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0060_provider_templates"
down_revision = "0059_creative_tool_tasks"
branch_labels = depends_on = None


def upgrade():
    op.add_column("model_channel", sa.Column("provider_template_id", sa.String(80), nullable=True))
    op.create_table(
        "provider_template",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("definition", JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade():
    op.drop_column("model_channel", "provider_template_id")
    op.drop_table("provider_template")
