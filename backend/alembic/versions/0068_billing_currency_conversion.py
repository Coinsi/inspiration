"""Snapshot optional USD membership to CNY payment conversion."""

import sqlalchemy as sa

from alembic import op

revision = "0068_billing_currency_conversion"
down_revision = "0067_membership_monthly_link"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "billing_settings",
        sa.Column("subscription_usd_to_cny_rate", sa.Numeric(12, 6), nullable=True),
    )
    op.create_check_constraint(
        "ck_billing_subscription_rate",
        "billing_settings",
        "subscription_usd_to_cny_rate is null or "
        "subscription_usd_to_cny_rate between 0 and 999999.999999",
    )
    op.add_column(
        "billing_order", sa.Column("original_amount_minor", sa.BigInteger(), nullable=True)
    )
    op.add_column("billing_order", sa.Column("conversion_rate", sa.Numeric(12, 6), nullable=True))
    op.create_check_constraint(
        "ck_billing_order_conversion",
        "billing_order",
        "(original_amount_minor is null and conversion_rate is null) or "
        "(original_amount_minor is not null and conversion_rate is not null "
        "and plan_id is not null and currency = 'cny' "
        "and original_amount_minor between 1 and 1000000000 "
        "and conversion_rate > 0 and conversion_rate <= 999999.999999 "
        "and amount_minor between 1 and 1000000000 "
        "and amount_minor = round(original_amount_minor * conversion_rate))",
    )


def downgrade():
    op.drop_constraint("ck_billing_order_conversion", "billing_order", type_="check")
    op.drop_column("billing_order", "conversion_rate")
    op.drop_column("billing_order", "original_amount_minor")
    op.drop_constraint("ck_billing_subscription_rate", "billing_settings", type_="check")
    op.drop_column("billing_settings", "subscription_usd_to_cny_rate")
