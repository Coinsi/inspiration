"""Timeline media instances need not belong to a storyboard shot."""

from alembic import op
import sqlalchemy as sa

revision = "0042_independent_timeline_clips"
down_revision = "0041_engineering_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("timeline_item", "shot_id", nullable=True)
    op.create_check_constraint(
        "ck_timeline_item_source",
        "timeline_item",
        "shot_id IS NOT NULL OR generation_id IS NOT NULL",
    )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM timeline_item WHERE shot_id IS NULL")):
        raise RuntimeError("Independent clips exist; migrate their references before downgrading")
    op.drop_constraint("ck_timeline_item_source", "timeline_item", type_="check")
    op.alter_column("timeline_item", "shot_id", nullable=False)
