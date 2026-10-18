"""Independent document identities and immutable content revisions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0041_engineering_documents"
down_revision = "0040_work_timeline_links"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade():
    op.add_column("work_timeline", sa.Column("document_revision", sa.Integer()))
    op.create_table(
        "engineering_document",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("work_id", UUID(), sa.ForeignKey("creative_work.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source_timeline_id", UUID(), sa.ForeignKey("timeline.id")),
        *timestamps(),
        sa.UniqueConstraint("work_id", "source_timeline_id"),
    )
    op.create_index("ix_engineering_document_work_id", "engineering_document", ["work_id"])
    op.create_table(
        "engineering_document_revision",
        sa.Column(
            "document_id", UUID(), sa.ForeignKey("engineering_document.id"), primary_key=True
        ),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("inputs", JSONB(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_by", UUID(), nullable=False),
        *timestamps(),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM engineering_document LIMIT 1")).first():
        raise RuntimeError("Engineering documents exist; export before downgrade")
    op.drop_table("engineering_document_revision")
    op.drop_index("ix_engineering_document_work_id", table_name="engineering_document")
    op.drop_table("engineering_document")
    op.drop_column("work_timeline", "document_revision")
