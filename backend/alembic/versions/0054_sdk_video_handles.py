"""SDK video handles may encode a provider, model and long operation resource name."""

import sqlalchemy as sa

from alembic import op

revision = "0054_sdk_video_handles"
down_revision = "0053_harness_retention_guards"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "generation_job",
        "external_job_id",
        type_=sa.Text(),
        existing_type=sa.String(255),
        existing_nullable=True,
    )


def downgrade():
    # Narrow only when safe; PostgreSQL rejects long handles rather than truncating them.
    op.alter_column(
        "generation_job",
        "external_job_id",
        type_=sa.String(255),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
