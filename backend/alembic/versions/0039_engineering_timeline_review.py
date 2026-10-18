"""Reviewed timeline updates and their fixed preview job."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from alembic import op

revision = "0039_engineering_timeline_review"
down_revision = "0038_engineering_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("engineering_change", sa.Column(
        "timeline_update", pg.JSONB(), nullable=False, server_default="{}"
    ))
    op.add_column("engineering_change", sa.Column(
        "preview_job_id", pg.UUID(), sa.ForeignKey("generation_job.id")
    ))


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM engineering_change WHERE timeline_update != '{}'::jsonb LIMIT 1"
    )).first():
        raise RuntimeError("Reviewed timeline changes exist; export them before downgrade")
    op.drop_column("engineering_change", "preview_job_id")
    op.drop_column("engineering_change", "timeline_update")
