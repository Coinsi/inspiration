"""Persistent, fixed-revision archive export jobs."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0045_engineering_archive_jobs"
down_revision = "0044_storage_deletion"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "engineering_archive_job",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("work_id", UUID(), sa.ForeignKey("creative_work.id"), nullable=False),
        sa.Column("created_by", UUID(), nullable=False),
        sa.Column("request_key", UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("progress", JSONB(), nullable=False),
        sa.Column("error", sa.String(255)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("work_id", "created_by", "request_key"),
    )
    op.create_index("ix_engineering_archive_job_work_id", "engineering_archive_job", ["work_id"])
    op.create_index("ix_engineering_archive_job_status", "engineering_archive_job", ["status"])
    from app.storage.gc_guards import install_v1

    install_v1(op.get_bind())


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM engineering_archive_job")):
        raise RuntimeError("Preserve archive task history before downgrade")
    op.drop_table("engineering_archive_job")
