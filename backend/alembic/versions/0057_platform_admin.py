"""Independent administrator roles and account change concurrency."""

from alembic import op
import sqlalchemy as sa

revision = "0057_platform_admin"
down_revision = "0056_platform_models"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user", sa.Column("platform_role", sa.String(16), nullable=True))
    op.add_column(
        "user", sa.Column("admin_revision", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column("user", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE \"user\" SET platform_role = 'super' WHERE is_platform_admin = true")


def downgrade():
    op.drop_column("user", "last_login_at")
    op.drop_column("user", "admin_revision")
    op.drop_column("user", "platform_role")
