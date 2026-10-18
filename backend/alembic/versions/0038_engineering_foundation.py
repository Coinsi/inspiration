"""Work resource manifests and reviewed append-only engineering commits."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0038_engineering_foundation"
down_revision = "0037_material_display_names"
branch_labels = None
depends_on = None


def common():
    return [
        sa.Column("id", pg.UUID(), primary_key=True),
        sa.Column("work_id", pg.UUID(), sa.ForeignKey("creative_work.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade():
    op.alter_column("creative_work", "script_id", nullable=True)
    op.add_column(
        "creative_work",
        sa.Column("engineering_revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        "engineering_commit",
        *common(),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("parent_revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", pg.JSONB(), nullable=False),
        sa.Column("created_by", pg.UUID(), nullable=False),
        sa.UniqueConstraint("work_id", "revision"),
    )
    op.create_table(
        "engineering_change",
        *common(),
        sa.Column("created_by", pg.UUID(), nullable=False),
        sa.Column("request_key", pg.UUID(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", pg.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("committed_revision", sa.Integer()),
        sa.UniqueConstraint("work_id", "created_by", "request_key"),
    )
    op.create_table(
        "engineering_resource",
        *common(),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_id", pg.UUID(), nullable=False),
        sa.Column("media_version_id", pg.UUID(), sa.ForeignKey("media_version.id")),
        sa.Column("generation_id", pg.UUID(), sa.ForeignKey("generation.id")),
        sa.Column("blob_hash", sa.String(128), sa.ForeignKey("blob.hash"), nullable=False),
        sa.Column("media_type", sa.String(16), nullable=False),
        sa.UniqueConstraint("work_id", "source_kind", "source_id"),
    )
    for table in ("engineering_commit", "engineering_change", "engineering_resource"):
        op.create_index("ix_" + table + "_work_id", table, ["work_id"])
    op.create_index(
        "ix_engineering_resource_media_version_id", "engineering_resource", ["media_version_id"]
    )


def downgrade():
    # Do not silently delete standalone works to fit the older required script FK.
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM creative_work WHERE script_id IS NULL LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Standalone works exist; migrate them explicitly before downgrade")
    for table in ("engineering_resource", "engineering_change", "engineering_commit"):
        op.drop_table(table)
    op.drop_column("creative_work", "engineering_revision")
    op.alter_column("creative_work", "script_id", nullable=False)
