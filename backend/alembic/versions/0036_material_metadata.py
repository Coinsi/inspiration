"""Editable library metadata independent from immutable media."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0036_material_metadata"
down_revision = "0035_narrative_covers"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("library_media", "generated_material_state"):
        op.add_column(table, sa.Column("description", sa.Text(), nullable=False, server_default=""))
        op.add_column(
            table, sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]")
        )
        op.add_column(
            table, sa.Column("metadata_revision", sa.Integer(), nullable=False, server_default="1")
        )
    op.add_column("generated_material_state", sa.Column("name", sa.String(255), nullable=True))
    op.add_column(
        "generated_material_state", sa.Column("poster_hash", sa.String(64), nullable=True)
    )
    op.create_foreign_key(
        "fk_generated_material_poster",
        "generated_material_state",
        "blob",
        ["poster_hash"],
        ["hash"],
    )


def downgrade():
    op.drop_constraint(
        "fk_generated_material_poster", "generated_material_state", type_="foreignkey"
    )
    op.drop_column("generated_material_state", "poster_hash")
    op.drop_column("generated_material_state", "name")
    for table in ("library_media", "generated_material_state"):
        for column in ("metadata_revision", "tags", "description"):
            op.drop_column(table, column)
