"""Resumable archive upload sessions on persistent engineering jobs."""

import sqlalchemy as sa

from alembic import op

revision = "0046_engineering_archive_uploads"
down_revision = "0045_engineering_archive_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "engineering_archive_job",
        sa.Column("operation", sa.String(16), nullable=False, server_default="export"),
    )
    for name in ("expected_bytes", "uploaded_bytes"):
        op.add_column(
            "engineering_archive_job",
            sa.Column(name, sa.BigInteger(), nullable=False, server_default="0"),
        )
    op.add_column("engineering_archive_job", sa.Column("filename", sa.String(255)))


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM engineering_archive_job WHERE operation='import'")
    ):
        raise RuntimeError("Preserve import sessions before downgrade")
    for name in ("filename", "uploaded_bytes", "expected_bytes", "operation"):
        op.drop_column("engineering_archive_job", name)
