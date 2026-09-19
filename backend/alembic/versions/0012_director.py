"""Blocking scene history and exported camera references."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0012_director"
down_revision = "0011_skills_sources"
branch_labels = depends_on = None


def ts():
    return [
        sa.Column(n, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
        for n in ("created_at", "updated_at")
    ]


def upgrade():
    op.create_table(
        "director_scene",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("document", JSONB, nullable=False),
        sa.Column("created_by", UUID, nullable=False),
        *ts(),
    )
    op.create_index("ix_director_scene_project_id", "director_scene", ["project_id"])
    op.create_table(
        "director_revision",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "scene_id", UUID, sa.ForeignKey("director_scene.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("document", JSONB, nullable=False),
        sa.Column("created_by", UUID, nullable=False),
        *ts(),
    )
    op.create_index(
        "uq_director_revision", "director_revision", ["scene_id", "revision"], unique=True
    )
    op.create_table(
        "director_reference",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "scene_id", UUID, sa.ForeignKey("director_scene.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("request_key", UUID, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("generation_id", UUID, sa.ForeignKey("generation.id"), nullable=False),
        *ts(),
    )
    op.create_index(
        "uq_director_reference", "director_reference", ["scene_id", "request_key"], unique=True
    )


def downgrade():
    for t in ("director_reference", "director_revision", "director_scene"):
        op.drop_table(t)
