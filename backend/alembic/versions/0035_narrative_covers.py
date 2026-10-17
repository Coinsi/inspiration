"""Optional independently managed covers for stories and scripts."""

import sqlalchemy as sa

from alembic import op

revision = "0035_narrative_covers"
down_revision = "0034_creative_works"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("novel", "script"):
        op.add_column(table, sa.Column("cover_blob_hash", sa.String(64), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_cover_blob", table, "blob", ["cover_blob_hash"], ["hash"]
        )


def downgrade():
    for table in ("script", "novel"):
        op.drop_constraint(f"fk_{table}_cover_blob", table, type_="foreignkey")
        op.drop_column(table, "cover_blob_hash")
