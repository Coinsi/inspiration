"""Add separately expiring membership allowances without changing wallet history."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0066_billing_memberships"
down_revision = "0065_easypay_checkout"
branch_labels = depends_on = None


def _timestamps():
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def upgrade():
    op.create_table(
        "membership_plan",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("features", postgresql.JSONB(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("original_amount_minor", sa.BigInteger()),
        sa.Column("credits", sa.BigInteger(), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=False),
        sa.Column("duration_unit", sa.String(8), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "amount_minor > 0 and credits > 0 "
            "and (original_amount_minor is null or original_amount_minor >= amount_minor)",
            name="ck_membership_plan_amounts",
        ),
        sa.CheckConstraint(
            "duration > 0 and duration_unit in ('day','week','month') and "
            "duration * case duration_unit when 'week' then 7 when 'month' then 30 "
            "else 1 end <= 3650",
            name="ck_membership_plan_duration",
        ),
        sa.CheckConstraint("jsonb_typeof(features) = 'array'", name="ck_membership_plan_features"),
    )
    op.create_index("ix_membership_plan_catalog", "membership_plan", ["active", "sort_order"])
    op.alter_column("billing_order", "pack_id", existing_type=postgresql.UUID(), nullable=True)
    op.add_column("billing_order", sa.Column("plan_id", postgresql.UUID(as_uuid=True)))
    op.add_column("billing_order", sa.Column("membership_days", sa.Integer()))
    op.create_foreign_key(
        "fk_billing_order_membership_plan", "billing_order", "membership_plan", ["plan_id"], ["id"]
    )
    op.create_check_constraint(
        "ck_billing_order_product",
        "billing_order",
        "(pack_id is not null and plan_id is null and membership_days is null) or "
        "(pack_id is null and plan_id is not null and membership_days is not null "
        "and membership_days between 1 and 3650)",
    )
    op.create_index("ix_billing_order_plan_id", "billing_order", ["plan_id"])
    op.create_table(
        "membership_term",
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("billing_order.id"),
            primary_key=True,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=False
        ),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("membership_plan.id"),
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credits", sa.BigInteger(), nullable=False),
        sa.Column("available", sa.BigInteger(), nullable=False),
        sa.Column("reserved", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("expires_at > starts_at", name="ck_membership_term_period"),
        sa.CheckConstraint(
            "credits > 0 and available >= 0 and reserved >= 0 and available + reserved <= credits",
            name="ck_membership_term_balances",
        ),
        sa.CheckConstraint(
            "status in ('granted','refund_pending','refunded')", name="ck_membership_term_status"
        ),
    )
    op.create_index(
        "ix_membership_term_user_plan", "membership_term", ["user_id", "plan_id", "expires_at"]
    )
    op.create_index("ix_membership_term_user_expiry", "membership_term", ["user_id", "expires_at"])
    op.create_table(
        "membership_allocation",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_job.id"),
            primary_key=True,
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("membership_term.order_id"),
            primary_key=True,
        ),
        sa.Column("units", sa.BigInteger(), nullable=False),
        sa.Column("settled_units", sa.BigInteger()),
        *_timestamps(),
        sa.CheckConstraint(
            "units > 0 and (settled_units is null or settled_units between 0 and units)",
            name="ck_membership_allocation_units",
        ),
    )
    op.create_index("ix_membership_allocation_order", "membership_allocation", ["order_id"])


def downgrade():
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM billing_order WHERE plan_id IS NOT NULL)")
    ):
        raise RuntimeError(
            "Membership payment history exists. "
            "Back it up and migrate it before removing memberships."
        )
    op.drop_table("membership_allocation")
    op.drop_table("membership_term")
    op.drop_index("ix_billing_order_plan_id", table_name="billing_order")
    op.drop_constraint("ck_billing_order_product", "billing_order", type_="check")
    op.drop_constraint("fk_billing_order_membership_plan", "billing_order", type_="foreignkey")
    op.drop_column("billing_order", "membership_days")
    op.drop_column("billing_order", "plan_id")
    op.alter_column("billing_order", "pack_id", existing_type=postgresql.UUID(), nullable=False)
    op.drop_table("membership_plan")
