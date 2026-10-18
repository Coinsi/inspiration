"""Persist work links before any candidate is adopted."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0040_work_timeline_links"
down_revision = "0039_engineering_timeline_review"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("work_timeline",
        sa.Column("work_id", UUID(), sa.ForeignKey("creative_work.id"), primary_key=True),
        sa.Column("timeline_id", UUID(), sa.ForeignKey("timeline.id"), primary_key=True))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM work_timeline LIMIT 1")).first():
        raise RuntimeError("Work timeline links exist; export them before downgrade")
    op.drop_table("work_timeline")
