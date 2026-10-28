"""Separate platform model scope from project ownership without copying credentials."""

import sqlalchemy as sa

from alembic import op

revision = "0056_platform_models"
down_revision = "0055_conversation_attachments"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("model_channel", "provider_config", "creative_model"):
        op.alter_column(table, "project_id", nullable=True)
    op.create_index(
        "uq_platform_provider_name",
        "provider_config",
        ["provider_name"],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL"),
    )


def downgrade():
    # Never discard shared keys/configuration to make an older schema fit.
    bind = op.get_bind()
    for table in ("model_channel", "provider_config", "creative_model"):
        if bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE project_id IS NULL LIMIT 1")).first():
            raise RuntimeError("Remove or migrate platform model configurations before downgrade")
    op.drop_index("uq_platform_provider_name", table_name="provider_config")
    for table in ("model_channel", "provider_config", "creative_model"):
        op.alter_column(table, "project_id", nullable=False)
