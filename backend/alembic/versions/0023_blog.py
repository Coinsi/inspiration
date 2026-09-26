"""Public journal with isolated article drafts."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0023_blog"
down_revision = "0022_website"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "blog_post",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("slug", sa.String(120), unique=True, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("draft", JSONB, nullable=False),
        sa.Column("published", JSONB(none_as_null=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade():
    op.drop_table("blog_post")
