"""Persist chapter adaptation jobs and their results."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0058_script_generation_tasks"
down_revision = "0057_platform_admin"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "script_generation_task",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column(
            "project_id", UUID(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("chapter_id", UUID(), sa.ForeignKey("chapter.id"), nullable=False),
        sa.Column("created_by", UUID(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(255)),
        sa.Column("strategy", sa.String(32)),
        sa.Column("script_id", UUID(), sa.ForeignKey("script.id")),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_script_generation_task_project_id", "script_generation_task", ["project_id"]
    )
    op.create_index(
        "ix_script_task_chapter",
        "script_generation_task",
        ["project_id", "chapter_id", "created_at"],
    )


def downgrade():
    op.drop_table("script_generation_task")
