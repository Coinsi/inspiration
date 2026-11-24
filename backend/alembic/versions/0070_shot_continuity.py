"""Track narrative asset sources, visible shot inputs and independent batch work."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0070_shot_continuity"
down_revision = "0069_credit_grants"
branch_labels = depends_on = None


def identity_columns():
    return (
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def upgrade():
    op.add_column("setting", sa.Column("source_notes", JSONB, nullable=False, server_default="{}"))
    op.add_column(
        "setting_extraction",
        sa.Column("chapter_progress", JSONB, nullable=False, server_default="{}"),
    )
    op.add_column("shot_asset_ref", sa.Column("usage", JSONB, nullable=False, server_default="{}"))
    op.add_column("scene", sa.Column("source_key", sa.String(64)))
    op.add_column("scene", sa.Column("visual_brief", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_scene_source_key", "scene", ["source_key"])
    op.add_column("generation_job", sa.Column("shot_input_key", sa.String(64)))
    op.create_index("ix_generation_job_shot_input_key", "generation_job", ["shot_input_key"])
    op.create_table(
        "asset_story_source",
        *identity_columns(),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("asset_id", UUID, sa.ForeignKey("asset.id"), nullable=False),
        sa.Column("scope_id", UUID),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_id", UUID, nullable=False),
        sa.Column("source_key", sa.String(300), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("evidence", sa.Text, nullable=False),
    )
    op.create_index("ix_asset_story_source_project_id", "asset_story_source", ["project_id"])
    op.create_index("ix_asset_story_source_asset_id", "asset_story_source", ["asset_id"])
    op.create_index(
        "uq_asset_story_source",
        "asset_story_source",
        ["project_id", "source_type", "source_id", "source_key"],
        unique=True,
    )
    op.create_table(
        "shot_batch",
        *identity_columns(),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("scene_id", UUID, sa.ForeignKey("scene.id"), nullable=False),
        sa.Column("created_by", UUID, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("request_key", UUID, nullable=False),
        sa.Column("options", JSONB, nullable=False),
        sa.UniqueConstraint(
            "project_id", "created_by", "request_key", name="uq_shot_batch_request"
        ),
    )
    op.create_index("ix_shot_batch_project_id", "shot_batch", ["project_id"])
    op.create_index("ix_shot_batch_scene_id", "shot_batch", ["scene_id"])
    op.create_table(
        "shot_batch_item",
        *identity_columns(),
        sa.Column(
            "batch_id", UUID, sa.ForeignKey("shot_batch.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("shot_id", UUID, sa.ForeignKey("shot.id"), nullable=False),
        sa.Column("job_id", UUID, sa.ForeignKey("generation_job.id")),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.UniqueConstraint("batch_id", "shot_id", name="uq_shot_batch_item"),
    )
    op.create_index("ix_shot_batch_item_batch_id", "shot_batch_item", ["batch_id"])
    op.create_index("ix_shot_batch_item_state", "shot_batch_item", ["state"])


def downgrade():
    op.drop_column("setting", "source_notes")
    op.drop_column("setting_extraction", "chapter_progress")
    for table in ("shot_batch_item", "shot_batch", "asset_story_source"):
        op.drop_table(table)
    op.drop_column("generation_job", "shot_input_key")
    op.drop_column("scene", "source_key")
    op.drop_column("scene", "visual_brief")
    op.drop_column("shot_asset_ref", "usage")
