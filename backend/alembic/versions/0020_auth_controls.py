"""Persist token revocation and cross-process authentication limits."""

import sqlalchemy as sa

from alembic import op

revision = "0020_auth_controls"
down_revision = "0019_model_channels"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "revoked_token",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_revoked_token_expires_at", "revoked_token", ["expires_at"])
    op.create_table(
        "auth_attempt",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_attempt_expires_at", "auth_attempt", ["expires_at"])


def downgrade():
    op.drop_table("auth_attempt")
    op.drop_table("revoked_token")
