"""Project model channels; existing provider identities remain unchanged."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0019_model_channels"
down_revision = "0018_timeline_visuals"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_channel",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("endpoint", sa.String(512), nullable=False, server_default=""),
        sa.Column("credentials_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade():
    # Models still use provider_config. Restore credentials before removing channel grouping.
    op.execute("""UPDATE provider_config p SET endpoint=c.endpoint,
        credentials_encrypted=c.credentials_encrypted,
        config=p.config - 'channel_id'
        FROM model_channel c WHERE p.config->>'channel_id'=c.id::text
        AND p.project_id=c.project_id""")
    op.drop_table("model_channel")
