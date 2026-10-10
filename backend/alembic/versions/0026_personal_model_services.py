"""Private account model services shared across the owner's projects."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "0026_personal_model_services"
down_revision = "0025_workbench_creation"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "personal_model_service",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("preset_id", sa.String(60), nullable=False),
        sa.Column("endpoint", sa.String(512), nullable=False),
        sa.Column("credentials_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("models", JSONB(), nullable=False),
        sa.Column("verification", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_personal_model_service_user_id", "personal_model_service", ["user_id"])


def downgrade():
    op.drop_table("personal_model_service")
