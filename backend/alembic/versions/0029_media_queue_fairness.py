"""Persist project dispatch history for fair media provider scheduling."""

import sqlalchemy as sa

from alembic import op

revision = "0029_media_queue_fairness"
down_revision = "0028_library_media_types"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "media_processing_run",
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Historical workers did not record dispatch times. Preserve their last
    # known activity as a conservative scheduling baseline, not a claimed
    # inference start time. Untouched queue entries remain undispatched.
    op.execute("UPDATE media_processing_run SET last_dispatched_at = updated_at WHERE attempts > 0")


def downgrade():
    op.drop_column("media_processing_run", "last_dispatched_at")
