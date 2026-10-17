"""Separate untouched upload display names from original filenames."""

from alembic import op

revision = "0037_material_display_names"
down_revision = "0036_material_metadata"
branch_labels = None
depends_on = None


def upgrade():
    # Only defaults matching the first source filename; never replace edited names.
    op.execute(r"""
        UPDATE library_media AS m
        SET name = COALESCE(NULLIF(btrim(regexp_replace(btrim(v.filename),
            '\.(mp4|mov|mkv|webm|jpg|jpeg|png|webp|mp3|wav|m4a|flac|ogg)$', '', 'i')), ''),
            '未命名素材'), metadata_revision = m.metadata_revision + 1
        FROM media_version AS v
        WHERE v.media_id = m.id AND v.ordinal = 1
            AND m.metadata_revision = 1 AND m.name = btrim(v.filename)
            AND v.filename ~* '\.(mp4|mov|mkv|webm|jpg|jpeg|png|webp|mp3|wav|m4a|flac|ogg)\s*$'
    """)


def downgrade():
    # Display names are user data; keep them when downgrading.
    pass
