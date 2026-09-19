"""Versioned creative skills and source catalogue imports."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0011_skills_sources"
down_revision = "0010_agent"
branch_labels = depends_on = None


def ts():
    return [
        sa.Column(n, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
        for n in ("created_at", "updated_at")
    ]


def upgrade():
    op.add_column(
        "agent_run", sa.Column("skill_snapshot", JSONB, nullable=False, server_default="[]")
    )
    op.create_table(
        "creative_skill",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("archived", sa.Boolean, nullable=False),
        sa.Column("document", JSONB, nullable=False),
        sa.Column("created_by", UUID, nullable=False),
        *ts(),
    )
    op.create_index("ix_creative_skill_project_id", "creative_skill", ["project_id"])
    op.create_table(
        "creative_skill_version",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "skill_id", UUID, sa.ForeignKey("creative_skill.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("document", JSONB, nullable=False),
        sa.Column("created_by", UUID, nullable=False),
        *ts(),
    )
    op.create_index(
        "uq_creative_skill_revision",
        "creative_skill_version",
        ["skill_id", "revision"],
        unique=True,
    )
    op.create_table(
        "source_import",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("request_key", UUID, nullable=False),
        sa.Column("source", sa.String(1000), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("asset_ids", JSONB, nullable=False),
        sa.Column("created_by", UUID, nullable=False),
        *ts(),
    )
    op.create_index("ix_source_import_project_id", "source_import", ["project_id"])
    op.create_index("uq_source_import", "source_import", ["project_id", "request_key"], unique=True)


def downgrade():
    for t in ("source_import", "creative_skill_version", "creative_skill"):
        op.drop_table(t)
    op.drop_column("agent_run", "skill_snapshot")
