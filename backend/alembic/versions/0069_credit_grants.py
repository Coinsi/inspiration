"""Track complimentary credits separately from protected purchased balances."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0069_credit_grants"
down_revision = "0068_billing_currency_conversion"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "quota", sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "billing_settings",
        sa.Column("welcome_credits", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_billing_welcome_credits", "billing_settings", "welcome_credits between 0 and 1000000"
    )
    for table, columns in (
        ("credit_account", ("grant_available", "grant_reserved")),
        ("credit_entry", ("grant_available_delta", "grant_reserved_delta")),
        ("credit_reservation", ("grant_units",)),
    ):
        for name in columns:
            op.add_column(
                table, sa.Column(name, sa.BigInteger(), nullable=False, server_default="0")
            )
    op.add_column(
        "credit_account",
        sa.Column("welcome_processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_credit_account_grants",
        "credit_account",
        "grant_available between 0 and available and grant_reserved between 0 and reserved",
    )
    op.create_check_constraint(
        "ck_credit_reservation_grants", "credit_reservation", "grant_units between 0 and units"
    )
    op.create_table(
        "credit_adjustment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("credit_account.user_id"),
            nullable=False,
        ),
        sa.Column(
            "actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=False
        ),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(8), nullable=False),
        sa.Column("units", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_credit_adjustment_idempotency"),
        sa.CheckConstraint(
            "operation in ('grant','revoke')", name="ck_credit_adjustment_operation"
        ),
        sa.CheckConstraint("units > 0 and units <= 10000000000", name="ck_credit_adjustment_units"),
    )
    op.create_index(
        "ix_credit_adjustment_user_created", "credit_adjustment", ["user_id", "created_at"]
    )


def downgrade():
    # Dropping provenance after gifts have been issued would silently turn any
    # remaining gifts into refundable purchased credit in the older application.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM credit_adjustment)
               OR EXISTS (SELECT 1 FROM credit_account
                          WHERE grant_available <> 0 OR grant_reserved <> 0)
               OR EXISTS (SELECT 1 FROM credit_entry
                          WHERE grant_available_delta <> 0 OR grant_reserved_delta <> 0)
            THEN
                RAISE EXCEPTION 'Cannot remove credit grant provenance after credits were issued';
            END IF;
        END $$;
    """)
    op.drop_table("credit_adjustment")
    op.drop_constraint("ck_credit_reservation_grants", "credit_reservation", type_="check")
    op.drop_constraint("ck_credit_account_grants", "credit_account", type_="check")
    op.drop_column("credit_account", "welcome_processed_at")
    for table, columns in (
        ("credit_reservation", ("grant_units",)),
        ("credit_entry", ("grant_available_delta", "grant_reserved_delta")),
        ("credit_account", ("grant_available", "grant_reserved")),
    ):
        for name in columns:
            op.drop_column(table, name)
    op.drop_constraint("ck_billing_welcome_credits", "billing_settings", type_="check")
    op.drop_column("billing_settings", "welcome_credits")
    op.drop_column("quota", "blocked")
