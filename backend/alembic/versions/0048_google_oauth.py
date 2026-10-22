"""Short-lived, single-use Google OAuth browser transactions."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0048_google_oauth"
down_revision = "0047_archive_capacity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "oauth_transaction",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oauth_transaction_expires_at", "oauth_transaction", ["expires_at"])


def downgrade():
    op.drop_table("oauth_transaction")
