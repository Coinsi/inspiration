"""Project-scoped video folders without changing source versions or usages."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0016_media_folders"
down_revision = "0015_media_reuse"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "media_folder",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("parent_id", UUID, sa.ForeignKey("media_folder.id")),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        *[
            sa.Column(n, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for n in ("created_at", "updated_at")
        ],
    )
    op.create_index("ix_media_folder_project_id", "media_folder", ["project_id"])
    op.create_index("ix_media_folder_parent_id", "media_folder", ["parent_id"])
    op.add_column("library_media", sa.Column("folder_id", UUID, sa.ForeignKey("media_folder.id")))
    op.create_index("ix_library_media_folder_id", "library_media", ["folder_id"])


def downgrade():
    op.drop_index("ix_library_media_folder_id", table_name="library_media")
    op.drop_column("library_media", "folder_id")
    op.drop_table("media_folder")
