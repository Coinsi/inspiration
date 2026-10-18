"""Persist engineering task delivery in the candidate/job transaction."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0043_engineering_delivery"
down_revision = "0042_independent_timeline_clips"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "engineering_delivery",
        sa.Column("job_id", UUID(), sa.ForeignKey("generation_job.id"), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_error", sa.String(255)),
        sa.Column("lease_token", UUID()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_engineering_delivery_next_attempt_at", "engineering_delivery", ["next_attempt_at"]
    )

    op.create_index(
        "ix_engineering_delivery_lease_expires_at", "engineering_delivery", ["lease_expires_at"]
    )
    op.execute(
        sa.text("""
        INSERT INTO engineering_delivery (job_id, state, attempts)
        SELECT id, 'pending', 0 FROM generation_job
        WHERE provider = 'local' AND status = 'pending'
          AND input_snapshot->>'engineering_change_id' IS NOT NULL
          AND input_snapshot->>'operation' IN ('render', 'engineering_image', 'engineering_render')
    """)
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM engineering_delivery d "
            "JOIN generation_job j ON j.id = d.job_id "
            "WHERE j.status IN ('pending', 'submitted', 'running')"
        )
    ):
        raise RuntimeError("Pending engineering deliveries exist; drain or cancel them first")
    op.drop_table("engineering_delivery")
