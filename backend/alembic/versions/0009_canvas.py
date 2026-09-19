"""Durable project canvases and execution batches."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0009_canvas"
down_revision = "0008_timeline_tracks"
branch_labels = depends_on = None


def timestamps():
    return [
        sa.Column(n, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
        for n in ("created_at", "updated_at")
    ]


def upgrade():
    op.create_table(
        "canvas",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("document", JSONB, nullable=False),
        sa.Column("created_by", UUID, nullable=False),
        *timestamps(),
    )
    op.create_index("ix_canvas_project_id", "canvas", ["project_id"])
    op.create_table(
        "canvas_revision",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "canvas_id", UUID, sa.ForeignKey("canvas.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("document", JSONB, nullable=False),
        sa.Column("created_by", UUID, nullable=False),
        *timestamps(),
    )
    op.create_index("uq_canvas_revision", "canvas_revision", ["canvas_id", "revision"], unique=True)
    op.create_table(
        "canvas_run",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "canvas_id", UUID, sa.ForeignKey("canvas.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("request_key", UUID, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("steps", JSONB, nullable=False),
        sa.Column("error", sa.String(1000)),
        sa.Column("created_by", UUID, nullable=False),
        *timestamps(),
    )
    op.create_index("ix_canvas_run_project_id", "canvas_run", ["project_id"])
    op.create_index(
        "uq_canvas_run_request", "canvas_run", ["canvas_id", "request_key"], unique=True
    )


def downgrade():
    for table in ("canvas_run", "canvas_revision", "canvas"):
        op.drop_table(table)
