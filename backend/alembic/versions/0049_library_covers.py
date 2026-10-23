"""Effect covers for prompts and fragments."""
from alembic import op
import sqlalchemy as sa
revision = "0049_library_covers"
down_revision = "0048_google_oauth"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("prompt", "prompt_fragment"):
        op.add_column(table, sa.Column("cover_image", sa.Text(), nullable=False, server_default=""))


def downgrade():
    for table in ("prompt", "prompt_fragment"):
        op.drop_column(table, "cover_image")
