"""Link annual membership display comparisons to a real monthly plan."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0067_membership_monthly_link"
down_revision = "0066_billing_memberships"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "membership_plan",
        sa.Column("monthly_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_membership_plan_monthly_plan",
        "membership_plan",
        "membership_plan",
        ["monthly_plan_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_membership_plan_monthly_link",
        "membership_plan",
        "monthly_plan_id is null or monthly_plan_id <> id",
    )
    op.create_index("ix_membership_plan_monthly_plan_id", "membership_plan", ["monthly_plan_id"])


def downgrade():
    op.drop_index("ix_membership_plan_monthly_plan_id", table_name="membership_plan")
    op.drop_constraint("ck_membership_plan_monthly_link", "membership_plan", type_="check")
    op.drop_constraint("fk_membership_plan_monthly_plan", "membership_plan", type_="foreignkey")
    op.drop_column("membership_plan", "monthly_plan_id")
