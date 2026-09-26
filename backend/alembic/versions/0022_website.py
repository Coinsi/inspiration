"""Platform website and independent administrator permission."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0022_website"
down_revision = "0021_account_identity"
branch_labels = None
depends_on = None


def stamps():
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade():
    op.add_column(
        "user",
        sa.Column("is_platform_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "website",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("published_revision", sa.Integer(), nullable=False),
        sa.Column("draft", JSONB, nullable=False),
        sa.Column("published", JSONB, nullable=False),
        *stamps(),
    )
    op.create_table(
        "website_release",
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("note", sa.String(300), nullable=False),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("user.id"), nullable=False),
        *stamps(),
    )
    op.create_table(
        "website_media",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("blob_hash", sa.String(64), sa.ForeignKey("blob.hash"), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("user.id"), nullable=False),
        *stamps(),
    )


def downgrade():
    for name in ("website_media", "website_release", "website"):
        op.drop_table(name)
    op.drop_column("user", "is_platform_admin")
