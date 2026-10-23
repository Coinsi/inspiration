"""Freeze the source and cost/endpoint policy of deferred ASR execution."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0052_transcription_execution"
down_revision = "0051_harness_engineering"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "transcription_run",
        sa.Column("execution_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
    )


def downgrade():
    op.drop_column("transcription_run", "execution_snapshot")
