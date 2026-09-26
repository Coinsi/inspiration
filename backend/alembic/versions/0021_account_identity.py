"""Account profile and independent external identities."""

import sqlalchemy as sa

from alembic import op

revision = "0021_account_identity"
down_revision = "0020_auth_controls"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user", sa.Column("avatar_data", sa.Text(), nullable=True))
    op.add_column("user", sa.Column("bio", sa.String(500), nullable=False, server_default=""))
    op.add_column(
        "user", sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.alter_column("user", "password_hash", nullable=True)
    op.create_table(
        "user_identity",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("provider", "issuer", "subject", name="uq_user_identity_subject"),
    )
    op.create_index("ix_user_identity_user_id", "user_identity", ["user_id"])


def downgrade():
    # Do not manufacture passwords for future external-only accounts.
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM "user" WHERE password_hash IS NULL')):
        raise RuntimeError("Cannot downgrade while external-only accounts exist")
    op.drop_table("user_identity")
    op.alter_column("user", "password_hash", nullable=False)
    for name in ("auth_version", "bio", "avatar_data"):
        op.drop_column("user", name)
