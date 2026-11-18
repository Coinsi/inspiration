"""Remember the provider template of an account-owned connection."""

import sqlalchemy as sa

from alembic import op

revision = "0061_personal_provider_template"
down_revision = "0060_provider_templates"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "personal_model_service", sa.Column("provider_template_id", sa.String(80), nullable=True)
    )
    op.add_column(
        "personal_model_service", sa.Column("imported_endpoint", sa.String(512), nullable=True)
    )
    # Existing addresses could only come from a fixed preset or an authorized project import.
    op.execute("UPDATE personal_model_service SET imported_endpoint = endpoint")


def downgrade():
    op.drop_column("personal_model_service", "imported_endpoint")
    op.drop_column("personal_model_service", "provider_template_id")
