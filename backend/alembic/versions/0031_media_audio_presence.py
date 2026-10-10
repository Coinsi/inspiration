"""Persist probed audio presence without guessing old video stream metadata."""

import sqlalchemy as sa

from alembic import op

revision = "0031_media_audio_presence"
down_revision = "0030_annotation_substring_index"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media_version", sa.Column("has_audio", sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column("media_version", "has_audio")
