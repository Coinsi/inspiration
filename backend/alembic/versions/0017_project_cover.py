"""Optional private project cover; existing projects keep their codes and data."""

import sqlalchemy as sa

from alembic import op

revision = "0017_project_cover"
down_revision = "0016_media_folders"
branch_labels = depends_on = None


def upgrade():
    op.add_column("project", sa.Column("cover_blob_hash", sa.String(64), nullable=True))
    op.create_foreign_key("fk_project_cover_blob", "project", "blob", ["cover_blob_hash"], ["hash"])


def downgrade():
    op.drop_constraint("fk_project_cover_blob", "project", type_="foreignkey")
    op.drop_column("project", "cover_blob_hash")
