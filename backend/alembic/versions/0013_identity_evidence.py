"""Reviewed identity evidence with immutable correction history."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0013_identity_evidence"
down_revision = "0012_director"
branch_labels = depends_on = None


def ts():
    return [
        sa.Column(n, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
        for n in ("created_at", "updated_at")
    ]


def upgrade():
    op.create_table(
        "story_identity",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("aliases", JSONB, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("asset_id", UUID, sa.ForeignKey("asset.id")),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("created_by", UUID, nullable=False),
        *ts(),
    )
    op.create_index("ix_story_identity_project_id", "story_identity", ["project_id"])
    op.create_table(
        "identity_evidence",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("identity_id", UUID, sa.ForeignKey("story_identity.id")),
        sa.Column("version_id", UUID, sa.ForeignKey("media_version.id"), nullable=False),
        sa.Column("start_ms", sa.BigInteger, nullable=False),
        sa.Column("end_ms", sa.BigInteger, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("observation", sa.Text, nullable=False),
        sa.Column("note", sa.Text, nullable=False),
        sa.Column("source", JSONB, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("created_by", UUID, nullable=False),
        *ts(),
    )
    for c in ("project_id", "identity_id", "version_id"):
        op.create_index("ix_identity_evidence_" + c, "identity_evidence", [c])
    op.create_table(
        "evidence_revision",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "evidence_id",
            UUID,
            sa.ForeignKey("identity_evidence.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("document", JSONB, nullable=False),
        sa.Column("created_by", UUID, nullable=False),
        *ts(),
    )
    op.create_index(
        "uq_evidence_revision", "evidence_revision", ["evidence_id", "revision"], unique=True
    )


def downgrade():
    for t in ("evidence_revision", "identity_evidence", "story_identity"):
        op.drop_table(t)
