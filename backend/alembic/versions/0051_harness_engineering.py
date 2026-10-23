"""Persist replayable candidate commands and their read/write prerequisites."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0051_harness_engineering"
down_revision = "0050_harness_runtime"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("canonical_commands", "read_preconditions", "write_scopes"):
        op.add_column(
            "engineering_change",
            sa.Column(name, postgresql.JSONB(), nullable=False, server_default="[]"),
        )
    op.add_column(
        "engineering_change",
        sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "engineering_change",
        sa.Column("rebased_from_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_engineering_change_rebased_from",
        "engineering_change",
        "engineering_change",
        ["rebased_from_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint(
        "fk_engineering_change_rebased_from", "engineering_change", type_="foreignkey"
    )
    for name in (
        "rebased_from_id",
        "provenance",
        "write_scopes",
        "read_preconditions",
        "canonical_commands",
    ):
        op.drop_column("engineering_change", name)
