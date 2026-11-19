"""Prepare browsing copies in the background for existing and future media."""

import sqlalchemy as sa

from alembic import op

revision = "0062_media_renditions"
down_revision = "0061_personal_provider_template"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "media_rendition",
        sa.Column(
            "source_hash",
            sa.String(128),
            sa.ForeignKey("blob.hash", ondelete="CASCADE"),
            primary_key=True,
        ),
        *(
            sa.Column(name, sa.String(128), sa.ForeignKey("blob.hash"))
            for name in ("thumbnail_hash", "display_hash", "playback_hash")
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_media_rendition_due",
        "media_rendition",
        ["due_at"],
        postgresql_where=sa.text("due_at IS NOT NULL"),
    )
    op.execute("""CREATE TRIGGER guard_blob_reference BEFORE INSERT OR UPDATE ON media_rendition
                  FOR EACH ROW EXECUTE FUNCTION guard_blob_reference()""")
    # Only enqueue identities here. No object storage or media processing during deployment.
    op.execute("""
        INSERT INTO media_rendition (source_hash)
        SELECT blob.hash FROM blob JOIN (
            SELECT output_blob_hash AS hash FROM generation
              WHERE output_type IN ('image','video') AND provider NOT IN ('mock','library','upload')
            UNION SELECT blob_hash FROM conversation_attachment
              WHERE status='ready' AND kind IN ('image','video')
            UNION SELECT poster_hash FROM media_version WHERE status='ready'
            UNION SELECT poster_hash FROM generated_material_state
            UNION SELECT representative_blob_hash FROM asset
            UNION SELECT cover_blob_hash FROM project
        ) AS media ON blob.hash=media.hash
        WHERE blob.mime LIKE 'image/%' OR blob.mime LIKE 'video/%'
        ON CONFLICT DO NOTHING
    """)


def downgrade():
    op.drop_table("media_rendition")
