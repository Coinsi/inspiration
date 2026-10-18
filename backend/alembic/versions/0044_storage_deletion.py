"""Durable physical deletion identity and reference guards (GC opt-in)."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0044_storage_deletion"
down_revision = "0043_engineering_delivery"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "blob_deletion",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("blob_hash", sa.String(128), nullable=False),
        sa.Column("storage_uri", sa.String(512), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(100)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for field in ("blob_hash", "storage_uri", "status"):
        op.create_index("ix_blob_deletion_" + field, "blob_deletion", [field])
    from app.storage.gc_guards import install_v1

    install_v1(op.get_bind())


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM blob_deletion")):
        raise RuntimeError("Deletion history must be preserved; downgrade refused")
    op.execute(sa.text("DROP FUNCTION guard_blob_reference() CASCADE"))
    op.drop_table("blob_deletion")
