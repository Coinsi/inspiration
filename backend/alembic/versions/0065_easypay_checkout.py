"""Preserve EasyPay checkout parameters across retries."""

import sqlalchemy as sa

from alembic import op

revision = "0065_easypay_checkout"
down_revision = "0064_billing"
branch_labels = depends_on = None


def upgrade():
    op.add_column("billing_order", sa.Column("checkout_ip", sa.String(45)))
    op.add_column("billing_order", sa.Column("checkout_mobile", sa.Boolean()))


def downgrade():
    op.drop_column("billing_order", "checkout_mobile")
    op.drop_column("billing_order", "checkout_ip")
