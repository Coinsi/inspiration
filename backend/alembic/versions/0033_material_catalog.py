"""Generated material visibility; original generations and references stay stable."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0033_material_catalog"
down_revision = "0032_conversations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "generated_material_state",
        sa.Column(
            "generation_id",
            pg.UUID(),
            sa.ForeignKey("generation.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade():
    op.drop_table("generated_material_state")
