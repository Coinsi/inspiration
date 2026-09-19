"""Add timeline visual overlays."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0018_timeline_visuals"
down_revision = "0017_project_cover"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("timeline", sa.Column("visuals", JSONB(), nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("timeline", "visuals")
