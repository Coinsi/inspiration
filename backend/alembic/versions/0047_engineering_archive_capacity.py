"""Archive capacity reservations survive workers and retries."""

import sqlalchemy as sa

from alembic import op

revision = "0047_archive_capacity"
down_revision = "0046_engineering_archive_uploads"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "engineering_archive_job",
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM engineering_archive_job WHERE reserved_bytes>0")
    ):
        raise RuntimeError("Release archive reservations before downgrade")
    op.drop_column("engineering_archive_job", "reserved_bytes")
