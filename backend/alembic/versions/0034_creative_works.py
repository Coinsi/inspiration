"""Explicit work ownership without reclassifying historical conversations."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from alembic import op

revision = "0034_creative_works"
down_revision = "0033_material_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "creative_work",
        sa.Column("id", pg.UUID(), primary_key=True),
        sa.Column(
            "project_id", pg.UUID(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("script_id", pg.UUID(), sa.ForeignKey("script.id"), nullable=False, unique=True),
        sa.Column("created_by", pg.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_creative_work_project_id", "creative_work", ["project_id"])
    for table in ("conversation", "workbench_creation"):
        op.add_column(table, sa.Column("work_id", pg.UUID(), sa.ForeignKey("creative_work.id")))


def downgrade():
    for table in ("workbench_creation", "conversation"):
        op.drop_column(table, "work_id")
    op.drop_table("creative_work")
