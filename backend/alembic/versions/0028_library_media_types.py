"""Independent image/audio library versions and timeless image references."""

import sqlalchemy as sa

from alembic import op

revision = "0028_library_media_types"
down_revision = "0027_media_processing"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "media_version",
        sa.Column("media_type", sa.String(16), nullable=False, server_default="video"),
    )
    op.create_check_constraint(
        "ck_media_version_type", "media_version", "media_type IN ('video','image','audio')"
    )
    op.drop_constraint("ck_media_usage_range", "media_usage", type_="check")
    op.alter_column("media_usage", "start_ms", nullable=True)
    op.alter_column("media_usage", "end_ms", nullable=True)
    op.create_check_constraint(
        "ck_media_usage_range",
        "media_usage",
        "(start_ms IS NULL AND end_ms IS NULL) OR "
        "(start_ms IS NOT NULL AND end_ms IS NOT NULL AND start_ms >= 0 AND end_ms > start_ms)",
    )
    op.create_index(
        "uq_media_usage_whole",
        "media_usage",
        ["version_id", "shot_id", "purpose"],
        unique=True,
        postgresql_where=sa.text("start_ms IS NULL AND end_ms IS NULL"),
    )


def downgrade():
    # Never invent time ranges or discard uploaded media to make rollback succeed.
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM media_version WHERE media_type <> 'video') "
            "OR EXISTS(SELECT 1 FROM media_usage WHERE start_ms IS NULL OR end_ms IS NULL)"
        )
    ):
        raise RuntimeError(
            "Cannot downgrade while image/audio media or whole-image references exist"
        )
    op.drop_index("uq_media_usage_whole", table_name="media_usage")
    op.drop_constraint("ck_media_usage_range", "media_usage", type_="check")
    op.alter_column("media_usage", "start_ms", nullable=False)
    op.alter_column("media_usage", "end_ms", nullable=False)
    op.create_check_constraint(
        "ck_media_usage_range", "media_usage", "start_ms >= 0 AND end_ms > start_ms"
    )
    op.drop_constraint("ck_media_version_type", "media_version", type_="check")
    op.drop_column("media_version", "media_type")
